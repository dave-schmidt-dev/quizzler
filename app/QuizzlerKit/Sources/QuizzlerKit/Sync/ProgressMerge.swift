import Foundation

/// Deterministic reducer for the shared progress document.
public enum ProgressMergeEngine {
    /// UTF-8 byte ordering is deliberate: String's locale/Unicode collation
    /// must never affect equal-revision convergence.
    public static func ordered(_ operations: [ProgressMergeOperation]) -> [ProgressMergeOperation] {
        operations.sorted {
            if $0.serverRevision != $1.serverRevision {
                return $0.serverRevision < $1.serverRevision
            }
            return Array($0.operationID.utf8).lexicographicallyPrecedes(Array($1.operationID.utf8))
        }
    }

    /// Applies a set of server-assigned operations. Replays are no-ops when
    /// their semantic payload matches; a reused ID with a changed payload is
    /// an explicit conflict and never mutates the snapshot.
    public static func merge(
        _ incoming: [ProgressMergeOperation],
        into original: ProgressMergeSnapshot,
        requireBaseRevision: Bool = false,
        now _: Date? = nil
    ) throws -> ProgressMergeResult {
        var snapshot = original
        var byID = Dictionary(uniqueKeysWithValues: snapshot.operations.map { ($0.operationID, $0) })
        let tombstones = Dictionary(uniqueKeysWithValues: snapshot.tombstones.map { ($0.operationID, $0) })
        let folded = Dictionary(uniqueKeysWithValues: snapshot.envelope.operations.map { ($0.id, $0) })
        var accepted: [ProgressMergeOperation] = []
        var duplicates: [String] = []
        var rebased = false
        var reviewEvents: [QuestionReviewEvent] = []
        for operation in ordered(incoming) {
            guard operation.serverRevision > 0,
                  operation.baseRevision >= 0 else { throw ProgressMergeError.invalidRevision }

            if let existing = byID[operation.operationID] {
                guard existing.semanticPayload == operation.semanticPayload,
                      existing.serverRevision == operation.serverRevision else {
                    throw ProgressMergeError.duplicateOperationPayloadMismatch
                }
                duplicates.append(operation.operationID)
                continue
            }
            if let existing = folded[operation.operationID] {
                guard existing.kind == operation.kind,
                      existing.session == operation.session,
                      existing.maximumLeitnerLevel == operation.maximumLeitnerLevel,
                      existing.createdAt == operation.createdAt,
                      existing.serverRevision == operation.serverRevision else {
                    throw ProgressMergeError.duplicateOperationPayloadMismatch
                }
                duplicates.append(operation.operationID)
                continue
            }
            if let tombstone = tombstones[operation.operationID] {
                guard tombstone.serverRevision == operation.serverRevision else {
                    throw ProgressMergeError.duplicateOperationPayloadMismatch
                }
                duplicates.append(operation.operationID)
                continue
            }

            // A compacted operation is already represented by the published
            // snapshot. This is the token-expired client's safe replay path.
            if operation.serverRevision <= snapshot.watermarkRevision {
                duplicates.append(operation.operationID)
                continue
            }

            accepted.append(operation)
            byID[operation.operationID] = operation
        }

        for operation in ordered(accepted) {
            let currentID = snapshot.envelope.operationID
            let isLateRevision = operation.serverRevision < snapshot.envelope.documentRevision
                || (operation.serverRevision == snapshot.envelope.documentRevision
                    && snapshot.envelope.documentRevision > 0
                    && !Array(currentID.utf8).lexicographicallyPrecedes(Array(operation.operationID.utf8)))
            if isLateRevision {
                throw ProgressMergeError.revisionConflict(
                    currentRevision: snapshot.envelope.documentRevision,
                    operationBaseRevision: operation.baseRevision
                )
            }
            let revisionMatches = operation.baseRevision == snapshot.envelope.documentRevision
            let orderingMatches = snapshot.envelope.documentRevision == 0
                || operation.baseOperationID == snapshot.envelope.operationID
            if requireBaseRevision && !revisionMatches {
                throw ProgressMergeError.revisionConflict(
                    currentRevision: snapshot.envelope.documentRevision,
                    operationBaseRevision: operation.baseRevision
                )
            }
            if requireBaseRevision && revisionMatches && !orderingMatches {
                throw ProgressMergeError.orderingConflict(
                    currentRevision: snapshot.envelope.documentRevision,
                    currentOperationID: snapshot.envelope.operationID
                )
            }
            if !revisionMatches || !orderingMatches {
                // Concurrent operations with the same base are ordered by the
                // server revision/ID pair. A genuinely stale operation is
                // still deterministic, but its caller must rebase before send.
                rebased = true
            }
            let priorRevision = snapshot.envelope.documentRevision
            var localOperation = ProgressOperation(
                operationID: operation.operationID,
                createdAt: operation.createdAt,
                status: .applied,
                kind: operation.kind,
                session: operation.session,
                maximumLeitnerLevel: operation.maximumLeitnerLevel
            )
            localOperation.serverRevision = operation.serverRevision
            localOperation.updatedAt = operation.kind == .review
                ? operation.session!.completedAt
                : operation.updatedAt
            reviewEvents.append(contentsOf: snapshot.envelope.applying(localOperation))
            snapshot.envelope.documentRevision = max(priorRevision, operation.serverRevision)
            snapshot.envelope.operationID = operation.operationID
        }
        snapshot.operations.append(contentsOf: accepted)
        snapshot.operations = ordered(snapshot.operations)
        return ProgressMergeResult(
            snapshot: snapshot,
            appliedOperationIDs: accepted.map(\.operationID),
            duplicateOperationIDs: duplicates,
            rebased: rebased,
            reviewEvents: reviewEvents
        )
    }

    /// Reconciles snapshots from different clients. A shared operation ID
    /// with a different payload is not safely mergeable and is surfaced for
    /// explicit manual resolution instead of silently choosing a winner.
    public static func reconcile(
        local: ProgressMergeSnapshot,
        remote: ProgressMergeSnapshot
    ) throws -> ProgressMergeResult {
        // Envelope snapshots contain folded facts which cannot be safely
        // reconstructed from a compacted operation log. Only merge logs when
        // both sides prove they share the same folded state and tombstones;
        // otherwise require explicit manual resolution.
        guard local.envelope == remote.envelope,
              Set(local.tombstones) == Set(remote.tombstones) else {
            throw ProgressMergeError.manualResolutionRequired
        }
        do {
            return try merge(remote.operations, into: local)
        } catch ProgressMergeError.duplicateOperationPayloadMismatch {
            throw ProgressMergeError.manualResolutionRequired
        }
    }

    public static func canonicalEvidenceHash(_ snapshot: ProgressMergeSnapshot) throws -> String {
        try snapshot.canonicalSemanticHash()
    }
}

public enum ProgressMergeLimits {
    public static let maximumSessionDetails = 200
    public static let maximumOperationRecords = 4_096
    public static let maximumTombstoneRecords = 4_096
    public static let maximumOperationAge: TimeInterval = 30 * 86_400
    /// Keep a strict margin below CloudKit's documented 250-record limit.
    public static let maximumRecordsPerBatch = 249
}

public enum ProgressCompactionStage: String, Codable, Sendable, Equatable {
    case snapshotPublished = "snapshot_published"
    case watermarkAdvanced = "watermark_advanced"
    case deletesBatched = "deletes_batched"
    case engineStatePersisted = "engine_state_persisted"
    case completed
}

public struct ProgressCompactionPlan: Codable, Sendable, Equatable {
    public let stage: ProgressCompactionStage
    public let snapshot: ProgressMergeSnapshot
    public let deletedOperationIDs: [String]
    public let deleteBatches: [[String]]

    public init(
        stage: ProgressCompactionStage,
        snapshot: ProgressMergeSnapshot,
        deletedOperationIDs: [String],
        deleteBatches: [[String]]
    ) {
        self.stage = stage
        self.snapshot = snapshot
        self.deletedOperationIDs = deletedOperationIDs
        self.deleteBatches = deleteBatches
    }
}

public enum ProgressCompactor {
    /// Computes a resumable compaction plan. The caller must publish the
    /// returned snapshot before applying its delete batches.
    public static func plan(
        snapshot original: ProgressMergeSnapshot,
        now: Date,
        pendingOperationIDs: Set<String> = []
    ) throws -> ProgressCompactionPlan {
        var snapshot = original
        let cutoff = now.addingTimeInterval(-ProgressMergeLimits.maximumOperationAge)
        let pending = Set(pendingOperationIDs)
        let sortedNewest = snapshot.operations.sorted {
            if $0.serverRevision != $1.serverRevision { return $0.serverRevision > $1.serverRevision }
            return Array($0.operationID.utf8).lexicographicallyPrecedes(Array($1.operationID.utf8))
        }
        let countExcess = Set(sortedNewest.dropFirst(ProgressMergeLimits.maximumOperationRecords).map(\.operationID))
        let ageExcess = Set(snapshot.operations.filter { $0.serverRecordedAt < cutoff }.map(\.operationID))
        let removeIDs = countExcess.union(ageExcess)

        // Pending records must remain retryable regardless of age/count.
        guard removeIDs.isDisjoint(with: pending) else {
            throw ProgressMergeError.pendingOperationWouldBePruned
        }
        let removed = snapshot.operations.filter { removeIDs.contains($0.operationID) }
        let covered = removed.map { ProgressMergeTombstone(operationID: $0.operationID, serverRevision: $0.serverRevision) }
        snapshot.tombstones.append(contentsOf: covered)
        snapshot.tombstones = snapshot.tombstones.sorted { $0.serverRevision == $1.serverRevision
            ? Array($0.operationID.utf8).lexicographicallyPrecedes(Array($1.operationID.utf8))
            : $0.serverRevision < $1.serverRevision }
        snapshot.operations.removeAll { removeIDs.contains($0.operationID) }
        // The folded envelope is also the published snapshot payload. Remove
        // compacted operation records there so a reload cannot resurrect them
        // into the next derived merge log.
        snapshot.envelope.operations.removeAll { removeIDs.contains($0.id) }

        // Only advance through revisions whose operations are known to be
        // covered. This avoids claiming a watermark across an unsent gap.
        var watermark = snapshot.watermarkRevision
        let removedRevisions = Set(removed.map(\.serverRevision))
        let retainedMinimumRevision = snapshot.operations.map(\.serverRevision).min() ?? Int.max
        let watermarkCeiling = retainedMinimumRevision == Int.max
            ? Int.max
            : max(snapshot.watermarkRevision, retainedMinimumRevision - 1)
        while watermark < watermarkCeiling && removedRevisions.contains(watermark + 1) {
            watermark += 1
        }
        snapshot.envelope.compaction.watermarkRevision = watermark
        snapshot.tombstones.removeAll { $0.serverRevision <= watermark }
        // A tombstone above the watermark is needed to reject a replay. It
        // cannot be evicted safely while the watermark is pinned, so refuse a
        // compaction that would exceed the bounded durable set.
        guard snapshot.tombstones.count <= ProgressMergeLimits.maximumTombstoneRecords else {
            throw ProgressMergeError.tombstoneRetentionExceeded
        }

        let IDs = removed.map(\.operationID).sorted {
            Array($0.utf8).lexicographicallyPrecedes(Array($1.utf8))
        }
        let batches = stride(from: 0, to: IDs.count, by: ProgressMergeLimits.maximumRecordsPerBatch).map { start in
            Array(IDs[start..<min(start + ProgressMergeLimits.maximumRecordsPerBatch, IDs.count)])
        }
        return ProgressCompactionPlan(
            stage: .snapshotPublished,
            snapshot: snapshot,
            deletedOperationIDs: IDs,
            deleteBatches: batches
        )
    }
}
