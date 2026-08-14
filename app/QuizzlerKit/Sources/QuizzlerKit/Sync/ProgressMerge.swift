import Foundation
import CryptoKit

/// An operation as it appears in the shared progress log.  The server
/// revision is the only ordering input supplied by a client; timestamps are
/// retained for audit and retention, but never decide which operation wins.
public struct ProgressMergeOperation: Codable, Sendable, Equatable, Identifiable {
    public let operationID: String
    public let baseRevision: Int
    public let baseOperationID: String?
    public let serverRevision: Int
    public let createdAt: Date
    public let updatedAt: Date
    /// Trusted ingest time used for retention; client timestamps are advisory.
    public let serverRecordedAt: Date
    public let session: SessionDetail

    public var id: String { operationID }

    public init(
        operationID: String,
        baseRevision: Int,
        baseOperationID: String? = nil,
        serverRevision: Int,
        createdAt: Date,
        updatedAt: Date? = nil,
        serverRecordedAt: Date? = nil,
        session: SessionDetail
    ) {
        precondition(!operationID.isEmpty, "operation IDs must not be empty")
        precondition(baseRevision >= 0, "base revisions must not be negative")
        precondition(serverRevision > 0, "server revisions start at one")
        self.operationID = operationID
        self.baseRevision = baseRevision
        self.baseOperationID = baseOperationID
        self.serverRevision = serverRevision
        self.createdAt = createdAt
        self.updatedAt = updatedAt ?? createdAt
        self.serverRecordedAt = serverRecordedAt ?? Date()
        self.session = session
    }

    public init(
        operation: ProgressOperation,
        baseRevision: Int,
        serverRevision: Int,
        serverRecordedAt: Date? = nil
    ) throws {
        guard !operation.id.isEmpty, baseRevision >= 0, serverRevision > 0 else {
            throw ProgressMergeError.invalidOperation
        }
        guard let session = operation.session else { throw ProgressMergeError.missingSession }
        self.init(
            operationID: operation.id,
            baseRevision: baseRevision,
            serverRevision: serverRevision,
            createdAt: operation.createdAt,
            updatedAt: operation.updatedAt,
            serverRecordedAt: serverRecordedAt,
            session: session
        )
    }

    private enum CodingKeys: String, CodingKey {
        case operationID, baseRevision, baseOperationID, serverRevision, createdAt, updatedAt, serverRecordedAt, session
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let operationID = try c.decode(String.self, forKey: .operationID)
        let baseRevision = try c.decode(Int.self, forKey: .baseRevision)
        let serverRevision = try c.decode(Int.self, forKey: .serverRevision)
        guard !operationID.isEmpty, baseRevision >= 0, serverRevision > 0 else {
            throw ProgressMergeError.invalidOperation
        }
        let updatedAt = try c.decode(Date.self, forKey: .updatedAt)
        self.init(
            operationID: operationID,
            baseRevision: baseRevision,
            baseOperationID: try c.decodeIfPresent(String.self, forKey: .baseOperationID),
            serverRevision: serverRevision,
            createdAt: try c.decode(Date.self, forKey: .createdAt),
            updatedAt: updatedAt,
            serverRecordedAt: try c.decodeIfPresent(Date.self, forKey: .serverRecordedAt) ?? updatedAt,
            session: try c.decode(SessionDetail.self, forKey: .session)
        )
    }

    /// The payload fields which an idempotent replay must preserve.
    public var semanticPayload: ProgressMergePayload {
        ProgressMergePayload(
            operationID: operationID,
            baseRevision: baseRevision,
            baseOperationID: baseOperationID,
            serverRevision: serverRevision,
            createdAt: createdAt,
            updatedAt: updatedAt,
            session: session
        )
    }
}

public struct ProgressMergePayload: Codable, Sendable, Equatable {
    public let operationID: String
    public let baseRevision: Int
    public let baseOperationID: String?
    public let serverRevision: Int
    public let createdAt: Date
    public let updatedAt: Date
    public let session: SessionDetail

    public init(
        operationID: String,
        baseRevision: Int = 0,
        baseOperationID: String? = nil,
        serverRevision: Int = 0,
        createdAt: Date = Date(timeIntervalSince1970: 0),
        updatedAt: Date = Date(timeIntervalSince1970: 0),
        session: SessionDetail
    ) {
        self.operationID = operationID
        self.baseRevision = baseRevision
        self.baseOperationID = baseOperationID
        self.serverRevision = serverRevision
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.session = session
    }
}

public struct ProgressMergeTombstone: Codable, Sendable, Equatable, Hashable {
    public let operationID: String
    public let serverRevision: Int

    public init(operationID: String, serverRevision: Int) {
        self.operationID = operationID
        self.serverRevision = serverRevision
    }
}

public struct ProgressMergeSnapshot: Codable, Sendable, Equatable {
    public var envelope: ProgressEnvelope
    public var operations: [ProgressMergeOperation]
    public var tombstones: [ProgressMergeTombstone]

    public init(
        envelope: ProgressEnvelope,
        operations: [ProgressMergeOperation] = [],
        tombstones: [ProgressMergeTombstone] = []
    ) throws {
        let operationIDs = Set(operations.map(\.operationID))
        let tombstoneIDs = Set(tombstones.map(\.operationID))
        guard Set(operations.map(\.operationID)).count == operations.count,
              Set(tombstones.map(\.operationID)).count == tombstones.count,
              Set(envelope.operations.map(\.id)).count == envelope.operations.count,
              operationIDs.isDisjoint(with: tombstoneIDs),
              Set(envelope.operations.compactMap { $0.serverRevision == nil ? nil : $0.id })
                  .isSubset(of: operationIDs),
              operations.allSatisfy({ $0.serverRevision > envelope.compaction.watermarkRevision }),
              tombstones.allSatisfy({ $0.serverRevision > 0 })
        else { throw ProgressMergeError.invalidSnapshot }
        let operationsByID = Dictionary(uniqueKeysWithValues: operations.map { ($0.operationID, $0) })
        guard envelope.operations.allSatisfy({ envelopeOperation in
            guard let mergeOperation = operationsByID[envelopeOperation.id] else { return true }
            return mergeOperation.session == envelopeOperation.session
                && mergeOperation.createdAt == envelopeOperation.createdAt
                && (envelopeOperation.serverRevision == nil
                    || envelopeOperation.serverRevision == mergeOperation.serverRevision)
        }) else { throw ProgressMergeError.invalidSnapshot }
        self.envelope = envelope
        self.operations = operations
        self.tombstones = tombstones
    }

    public var watermarkRevision: Int { envelope.compaction.watermarkRevision }

    public static func empty(
        actorID: String,
        createdAt: Date = Date(timeIntervalSince1970: 0),
        operationID: String = "baseline"
    ) -> ProgressMergeSnapshot {
        try! ProgressMergeSnapshot(envelope: ProgressEnvelope(
            actorID: actorID,
            operationID: operationID,
            createdAt: createdAt
        ))
    }

    private enum CodingKeys: String, CodingKey { case envelope, operations, tombstones }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let envelope = try c.decode(ProgressEnvelope.self, forKey: .envelope)
        let operations = try c.decode([ProgressMergeOperation].self, forKey: .operations)
        let tombstones = try c.decode([ProgressMergeTombstone].self, forKey: .tombstones)
        try self.init(
            envelope: envelope,
            operations: operations,
            tombstones: tombstones
        )
    }
}

public enum ProgressMergeError: Error, Codable, Sendable, Equatable {
    case invalidOperation
    case invalidSnapshot
    case invalidRevision
    case missingSession
    case duplicateOperationPayloadMismatch
    case revisionConflict(currentRevision: Int, operationBaseRevision: Int)
    case orderingConflict(currentRevision: Int, currentOperationID: String)
    case manualResolutionRequired
    case pendingOperationWouldBePruned
    case tombstoneRetentionExceeded
}

public struct ProgressMergeResult: Sendable, Equatable {
    public let snapshot: ProgressMergeSnapshot
    public let appliedOperationIDs: [String]
    public let duplicateOperationIDs: [String]
    public let rebased: Bool

    public init(
        snapshot: ProgressMergeSnapshot,
        appliedOperationIDs: [String],
        duplicateOperationIDs: [String],
        rebased: Bool
    ) {
        self.snapshot = snapshot
        self.appliedOperationIDs = appliedOperationIDs
        self.duplicateOperationIDs = duplicateOperationIDs
        self.rebased = rebased
    }
}

public struct ProgressMergeDivergence: Sendable, Equatable {
    public let local: ProgressMergeSnapshot
    public let remote: ProgressMergeSnapshot

    public init(local: ProgressMergeSnapshot, remote: ProgressMergeSnapshot) {
        self.local = local
        self.remote = remote
    }
}

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
                guard existing.session == operation.session,
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
                session: operation.session
            )
            localOperation.serverRevision = operation.serverRevision
            localOperation.updatedAt = operation.session.completedAt
            snapshot.envelope.applying(operation.session, operation: localOperation)
            snapshot.envelope.documentRevision = max(priorRevision, operation.serverRevision)
            snapshot.envelope.operationID = operation.operationID
        }
        snapshot.operations.append(contentsOf: accepted)
        snapshot.operations = ordered(snapshot.operations)
        return ProgressMergeResult(
            snapshot: snapshot,
            appliedOperationIDs: accepted.map(\.operationID),
            duplicateOperationIDs: duplicates,
            rebased: rebased
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

// MARK: - Canonical evidence

private enum ProgressCanonical {
    static func text(_ value: String) -> String {
        value.precomposedStringWithCanonicalMapping
    }

    static func milliseconds(_ date: Date) -> Int64 {
        Int64((date.timeIntervalSince1970 * 1_000).rounded(.toNearestOrEven))
    }

    static func identity(_ value: QuestionIdentity) -> CanonicalIdentity {
        CanonicalIdentity(
            courseID: text(value.courseID),
            packID: text(value.packID),
            questionID: text(value.questionID)
        )
    }

    static func identityPrecedes(_ left: QuestionIdentity, _ right: QuestionIdentity) -> Bool {
        let leftParts = [left.courseID, left.packID, left.questionID].map { Array(text($0).utf8) }
        let rightParts = [right.courseID, right.packID, right.questionID].map { Array(text($0).utf8) }
        for (l, r) in zip(leftParts, rightParts) {
            if l != r { return l.lexicographicallyPrecedes(r) }
        }
        return false
    }

    static func session(_ value: SessionDetail) -> CanonicalSession {
        CanonicalSession(
            id: text(value.id),
            completedAt: milliseconds(value.completedAt),
            answers: value.answers.map {
                CanonicalAnswer(identity: identity($0.identity), correct: $0.correct)
            }
        )
    }

    static func operation(_ value: ProgressOperation) -> CanonicalEnvelopeOperation {
        CanonicalEnvelopeOperation(
            id: text(value.id),
            serverRevision: value.serverRevision,
            createdAt: milliseconds(value.createdAt),
            updatedAt: milliseconds(value.updatedAt),
            status: text(value.status.rawValue),
            session: value.session.map(session),
            error: value.error.map { error in
                switch error {
                case .encodedSizeRefused: return "encoded_size_refused"
                case .failed: return "failed"
                }
            }
        )
    }

    static func srs(_ value: SRSSnapshot) -> CanonicalSRS {
        CanonicalSRS(
            identity: identity(value.identity),
            tier: value.state.tier,
            nextDueAt: milliseconds(value.state.nextDueAt),
            lastReviewedAt: value.state.lastReviewedAt.map(milliseconds),
            intervalDays: value.state.intervalDays,
            reviewCount: value.state.reviewCount
        )
    }

    static func issue(_ value: QuestionIssue) -> CanonicalIssue {
        CanonicalIssue(
            schemaVersion: QuestionIssue.schemaVersion,
            issueID: text(value.issueID),
            courseID: text(value.courseID),
            packID: text(value.packID),
            questionID: text(value.questionID),
            questionType: text(value.questionType.rawValue),
            appVersion: text(value.appVersion),
            build: text(value.build),
            selectedResponse: value.selectedResponse.map(text),
            description: text(value.description)
        )
    }
}

private struct CanonicalIdentity: Encodable {
    let courseID: String
    let packID: String
    let questionID: String
}

private struct CanonicalAnswer: Encodable {
    let identity: CanonicalIdentity
    let correct: Bool
}

private struct CanonicalSession: Encodable {
    let id: String
    let completedAt: Int64
    let answers: [CanonicalAnswer]
}

private struct CanonicalEnvelopeOperation: Encodable {
    let id: String
    let serverRevision: Int?
    let createdAt: Int64
    let updatedAt: Int64
    let status: String
    let session: CanonicalSession?
    let error: String?

    enum CodingKeys: String, CodingKey { case id, serverRevision, createdAt, updatedAt, status, session, error }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encodeIfPresent(serverRevision, forKey: .serverRevision)
        try c.encode(createdAt, forKey: .createdAt)
        try c.encode(updatedAt, forKey: .updatedAt)
        try c.encode(status, forKey: .status)
        try c.encodeIfPresent(session, forKey: .session)
        try c.encodeIfPresent(error, forKey: .error)
    }
}

private struct CanonicalMastery: Encodable {
    let identity: CanonicalIdentity
    let answered: Int
    let correct: Int
}

private struct CanonicalSRS: Encodable {
    let identity: CanonicalIdentity
    let tier: Int
    let nextDueAt: Int64
    let lastReviewedAt: Int64?
    let intervalDays: Int
    let reviewCount: Int

    enum CodingKeys: String, CodingKey { case identity, tier, nextDueAt, lastReviewedAt, intervalDays, reviewCount }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(identity, forKey: .identity)
        try c.encode(tier, forKey: .tier)
        try c.encode(nextDueAt, forKey: .nextDueAt)
        try c.encodeIfPresent(lastReviewedAt, forKey: .lastReviewedAt)
        try c.encode(intervalDays, forKey: .intervalDays)
        try c.encode(reviewCount, forKey: .reviewCount)
    }
}

private struct CanonicalIssue: Encodable {
    let schemaVersion: Int
    let issueID: String
    let courseID: String
    let packID: String
    let questionID: String
    let questionType: String
    let appVersion: String
    let build: String
    let selectedResponse: String?
    let description: String

    enum CodingKeys: String, CodingKey {
        case schemaVersion, issueID, courseID, packID, questionID, questionType
        case appVersion, build, selectedResponse, description
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(schemaVersion, forKey: .schemaVersion)
        try c.encode(issueID, forKey: .issueID)
        try c.encode(courseID, forKey: .courseID)
        try c.encode(packID, forKey: .packID)
        try c.encode(questionID, forKey: .questionID)
        try c.encode(questionType, forKey: .questionType)
        try c.encode(appVersion, forKey: .appVersion)
        try c.encode(build, forKey: .build)
        try c.encodeIfPresent(selectedResponse, forKey: .selectedResponse)
        try c.encode(description, forKey: .description)
    }
}

private struct CanonicalMergeOperation: Encodable {
    let operationID: String
    let baseRevision: Int
    let baseOperationID: String?
    let serverRevision: Int
    let createdAt: Int64
    let updatedAt: Int64
    let session: CanonicalSession
}

private struct CanonicalTombstone: Encodable {
    let operationID: String
    let serverRevision: Int
}

private struct CanonicalEnvelope: Encodable {
    let protocolName: String
    let schemaVersion: Int
    let documentRevision: Int
    let actorID: String
    let operationID: String
    let createdAt: Int64
    let sessionDetails: [CanonicalSession]
    let sessionsTotal: Int
    let answered: Int
    let correct: Int
    let mastery: [CanonicalMastery]
    let srs: [CanonicalSRS]
    let compactionVersion: Int
    let compactionWatermarkRevision: Int
    let operations: [CanonicalEnvelopeOperation]
    let issues: [CanonicalIssue]
}

private struct CanonicalSnapshot: Encodable {
    let envelope: CanonicalEnvelope
    let operations: [CanonicalMergeOperation]
    let tombstones: [CanonicalTombstone]
}

public extension ProgressMergeSnapshot {
    /// Canonical semantic bytes normalize strings to NFC, timestamps to
    /// integer milliseconds, and all set-like collections to UTF-8 order.
    func canonicalSemanticData() throws -> Data {
        let sortedMastery = envelope.mastery.sorted {
            ProgressCanonical.identityPrecedes($0.identity, $1.identity)
        }.map { CanonicalMastery(identity: ProgressCanonical.identity($0.identity), answered: $0.answered, correct: $0.correct) }
        let sortedSRS = envelope.srs.sorted {
            ProgressCanonical.identityPrecedes($0.identity, $1.identity)
        }.map(ProgressCanonical.srs)
        let sortedIssues = envelope.issues.sorted {
            Array(ProgressCanonical.text($0.issueID).utf8).lexicographicallyPrecedes(Array(ProgressCanonical.text($1.issueID).utf8))
        }.map(ProgressCanonical.issue)
        let sortedEnvelopeOperations = envelope.operations.sorted {
            Array(ProgressCanonical.text($0.id).utf8).lexicographicallyPrecedes(Array(ProgressCanonical.text($1.id).utf8))
        }.map(ProgressCanonical.operation)
        let sortedTombstones = tombstones.sorted {
            if $0.serverRevision != $1.serverRevision { return $0.serverRevision < $1.serverRevision }
            return Array(ProgressCanonical.text($0.operationID).utf8).lexicographicallyPrecedes(Array(ProgressCanonical.text($1.operationID).utf8))
        }.map { CanonicalTombstone(operationID: ProgressCanonical.text($0.operationID), serverRevision: $0.serverRevision) }
        let canonical = CanonicalSnapshot(
            envelope: CanonicalEnvelope(
                protocolName: ProgressCanonical.text(envelope.protocolName),
                schemaVersion: envelope.schemaVersion,
                documentRevision: envelope.documentRevision,
                actorID: ProgressCanonical.text(envelope.actorID),
                operationID: ProgressCanonical.text(envelope.operationID),
                createdAt: ProgressCanonical.milliseconds(envelope.createdAt),
                sessionDetails: envelope.sessionDetails.map(ProgressCanonical.session),
                sessionsTotal: envelope.aggregate.sessionsTotal,
                answered: envelope.aggregate.answered,
                correct: envelope.aggregate.correct,
                mastery: sortedMastery,
                srs: sortedSRS,
                compactionVersion: envelope.compaction.version,
                compactionWatermarkRevision: envelope.compaction.watermarkRevision,
                operations: sortedEnvelopeOperations,
                issues: sortedIssues
            ),
            operations: ProgressMergeEngine.ordered(operations).map {
                CanonicalMergeOperation(
                    operationID: ProgressCanonical.text($0.operationID),
                    baseRevision: $0.baseRevision,
                    baseOperationID: $0.baseOperationID.map(ProgressCanonical.text),
                    serverRevision: $0.serverRevision,
                    createdAt: ProgressCanonical.milliseconds($0.createdAt),
                    updatedAt: ProgressCanonical.milliseconds($0.updatedAt),
                    session: ProgressCanonical.session($0.session)
                )
            },
            tombstones: sortedTombstones
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        return try encoder.encode(canonical)
    }

    func canonicalSemanticHash() throws -> String {
        SHA256.hash(data: try canonicalSemanticData())
            .map { String(format: "%02x", $0) }
            .joined()
    }

    func canonicalEvidenceHash() throws -> String { try canonicalSemanticHash() }
}
