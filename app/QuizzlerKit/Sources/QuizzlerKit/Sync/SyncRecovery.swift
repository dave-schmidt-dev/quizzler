import Foundation

public enum SyncRecoveryStage: Codable, Sendable, Equatable, Hashable {
    case appendBeforeCompact
    case snapshotPublished
    case watermarkAdvanced
    case deleteBatch(Int)
    case engineStatePersisted
    case completed

    private enum CodingKeys: String, CodingKey { case kind, index }
    private enum Kind: String, Codable { case appendBeforeCompact, snapshotPublished, watermarkAdvanced, deleteBatch, engineStatePersisted, completed }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        switch try c.decode(Kind.self, forKey: .kind) {
        case .appendBeforeCompact: self = .appendBeforeCompact
        case .snapshotPublished: self = .snapshotPublished
        case .watermarkAdvanced: self = .watermarkAdvanced
        case .deleteBatch: self = .deleteBatch(try c.decode(Int.self, forKey: .index))
        case .engineStatePersisted: self = .engineStatePersisted
        case .completed: self = .completed
        }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        switch self {
        case .appendBeforeCompact: try c.encode(Kind.appendBeforeCompact, forKey: .kind)
        case .snapshotPublished: try c.encode(Kind.snapshotPublished, forKey: .kind)
        case .watermarkAdvanced: try c.encode(Kind.watermarkAdvanced, forKey: .kind)
        case .deleteBatch(let index):
            try c.encode(Kind.deleteBatch, forKey: .kind)
            try c.encode(index, forKey: .index)
        case .engineStatePersisted: try c.encode(Kind.engineStatePersisted, forKey: .kind)
        case .completed: try c.encode(Kind.completed, forKey: .kind)
        }
    }
}

public enum SyncRecoveryError: Error, Codable, Sendable, Equatable {
    case invalidCheckpoint
    case crashInjected(SyncRecoveryStage)
    case tokenExpired
    case rebaseRequired(currentRevision: Int, suppliedRevision: Int)
    case manualResolutionRequired
}

/// Everything needed to resume compaction after a process interruption. The
/// checkpoint is intentionally value-typed so it can be atomically persisted
/// beside CKSyncEngine state by CloudProgressRepository.
public struct SyncRecoveryCheckpoint: Codable, Sendable, Equatable {
    public var stage: SyncRecoveryStage
    public var snapshot: ProgressMergeSnapshot
    public var targetSnapshot: ProgressMergeSnapshot?
    public var compactionPlan: ProgressCompactionPlan?
    public var pendingOperations: [ProgressMergeOperation]
    public var pendingEngineOperationIDs: Set<String>
    public var compactionDate: Date
    public var engineStateGeneration: Int
    public var snapshotPublicationGeneration: Int
    public var watermarkGeneration: Int
    public var deletedOperationIDs: Set<String>
    public var tokenExpired: Bool

    public init(
        stage: SyncRecoveryStage,
        snapshot: ProgressMergeSnapshot,
        targetSnapshot: ProgressMergeSnapshot? = nil,
        compactionPlan: ProgressCompactionPlan? = nil,
        pendingOperations: [ProgressMergeOperation] = [],
        pendingEngineOperationIDs: Set<String> = [],
        compactionDate: Date = Date(),
        engineStateGeneration: Int = 0,
        snapshotPublicationGeneration: Int = 0,
        watermarkGeneration: Int = 0,
        deletedOperationIDs: Set<String> = [],
        tokenExpired: Bool = false
    ) {
        self.stage = stage
        self.snapshot = snapshot
        self.targetSnapshot = targetSnapshot
        self.compactionPlan = compactionPlan
        self.pendingOperations = pendingOperations
        self.pendingEngineOperationIDs = pendingEngineOperationIDs
        self.compactionDate = compactionDate
        self.engineStateGeneration = engineStateGeneration
        self.snapshotPublicationGeneration = snapshotPublicationGeneration
        self.watermarkGeneration = watermarkGeneration
        self.deletedOperationIDs = deletedOperationIDs
        self.tokenExpired = tokenExpired
    }
}

public enum SyncRecoveryMachine {
    /// Appends all new operations before planning compaction. This ordering is
    /// the crash-safety invariant: a just-arrived operation is in the durable
    /// snapshot before any old operation can be deleted.
    public static func begin(
        snapshot: ProgressMergeSnapshot,
        pendingOperations: [ProgressMergeOperation],
        pendingEngineOperationIDs: Set<String> = [],
        now: Date = Date()
    ) throws -> SyncRecoveryCheckpoint {
        let merged = try ProgressMergeEngine.merge(pendingOperations, into: snapshot, now: now)
        let appended = merged.snapshot
        let plan = try ProgressCompactor.plan(
            snapshot: appended,
            now: now,
            pendingOperationIDs: pendingEngineOperationIDs
        )
        return SyncRecoveryCheckpoint(
            stage: .appendBeforeCompact,
            snapshot: appended,
            targetSnapshot: plan.snapshot,
            compactionPlan: plan,
            pendingOperations: pendingOperations,
            pendingEngineOperationIDs: pendingEngineOperationIDs,
            compactionDate: now
        )
    }

    /// Advances exactly one durable stage. A crash injection throws before
    /// mutating the input value, so retrying the same checkpoint is safe.
    public static func step(
        _ checkpoint: SyncRecoveryCheckpoint,
        injectCrashAt: SyncRecoveryStage? = nil
    ) throws -> SyncRecoveryCheckpoint {
        guard let plan = checkpoint.compactionPlan,
              let target = checkpoint.targetSnapshot else {
            throw SyncRecoveryError.invalidCheckpoint
        }
        if let injectCrashAt, injectCrashAt == checkpoint.stage {
            throw SyncRecoveryError.crashInjected(checkpoint.stage)
        }

        var next = checkpoint
        switch checkpoint.stage {
        case .appendBeforeCompact:
            // Publish the compacted snapshot while retaining the old
            // watermark. No operation below the old watermark can remain in
            // this value, so every intermediate value still decodes safely.
            var published = target
            published.envelope.compaction.watermarkRevision = checkpoint.snapshot.watermarkRevision
            next.snapshot = try ProgressMergeSnapshot(
                envelope: published.envelope,
                operations: published.operations,
                tombstones: published.tombstones
            )
            next.snapshotPublicationGeneration += 1
            next.stage = .snapshotPublished
        case .snapshotPublished:
            // Advance the watermark only after publication has been recorded.
            next.snapshot = target
            next.watermarkGeneration += 1
            next.stage = .watermarkAdvanced
        case .watermarkAdvanced:
            if plan.deleteBatches.isEmpty {
                next.stage = .engineStatePersisted
            } else {
                next.stage = .deleteBatch(0)
            }
        case .deleteBatch(let index):
            guard index >= 0, index < plan.deleteBatches.count else {
                throw SyncRecoveryError.invalidCheckpoint
            }
            // The published snapshot already removed these operations. This
            // transition records the corresponding remote delete acknowledgement
            // and is intentionally distinct from publication/watermark steps.
            next.deletedOperationIDs.formUnion(plan.deleteBatches[index])
            let nextIndex = index + 1
            next.stage = nextIndex < plan.deleteBatches.count ? .deleteBatch(nextIndex) : .engineStatePersisted
        case .engineStatePersisted:
            next.engineStateGeneration += 1
            next.stage = .completed
        case .completed:
            return checkpoint
        }
        return next
    }

    /// Resumes until completion. A caller that needs live progress can call
    /// `step` itself; this convenience method retains the same stage order.
    public static func finish(_ initial: SyncRecoveryCheckpoint) throws -> SyncRecoveryCheckpoint {
        var checkpoint = initial
        while checkpoint.stage != .completed {
            checkpoint = try step(checkpoint)
        }
        return checkpoint
    }

    /// A stale base revision or an expired token must not send local changes.
    public static func requireFreshBase(
        snapshot: ProgressMergeSnapshot,
        suppliedBaseRevision: Int,
        suppliedOperationID: String? = nil
    ) throws {
        guard suppliedBaseRevision == snapshot.envelope.documentRevision else {
            throw SyncRecoveryError.rebaseRequired(
                currentRevision: snapshot.envelope.documentRevision,
                suppliedRevision: suppliedBaseRevision
            )
        }
        if suppliedBaseRevision > 0,
           suppliedOperationID != snapshot.envelope.operationID {
            throw SyncRecoveryError.rebaseRequired(
                currentRevision: snapshot.envelope.documentRevision,
                suppliedRevision: suppliedBaseRevision
            )
        }
    }

    /// Replaces the working snapshot after token expiry, then replays only
    /// still-pending operations in canonical order. The full snapshot itself
    /// is authoritative; a missing/expired token is never treated as empty.
    public static func recoverAfterTokenExpiry(
        fullSnapshot: ProgressMergeSnapshot,
        pendingOperations: [ProgressMergeOperation],
        pendingEngineOperationIDs: Set<String> = [],
        now: Date = Date()
    ) throws -> SyncRecoveryCheckpoint {
        let merged = try ProgressMergeEngine.merge(pendingOperations, into: fullSnapshot, now: now)
        let rebased = try ProgressCompactor.plan(
            snapshot: merged.snapshot,
            now: now,
            pendingOperationIDs: pendingEngineOperationIDs
        )
        return SyncRecoveryCheckpoint(
            stage: .appendBeforeCompact,
            snapshot: merged.snapshot,
            targetSnapshot: rebased.snapshot,
            compactionPlan: rebased,
            pendingOperations: pendingOperations,
            pendingEngineOperationIDs: pendingEngineOperationIDs,
            compactionDate: now,
            tokenExpired: true
        )
    }
}
