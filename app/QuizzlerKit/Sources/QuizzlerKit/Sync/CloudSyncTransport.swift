import Foundation

/// Boundary for the narrow CloudKit development probe. Production sync must
/// remain behind this protocol so unit tests never contact an iCloud account.
public protocol CloudSyncTransport: Sendable {
    func fetchChanges() async throws
    func sendChanges() async throws
}

/// Non-sensitive identifiers for the disposable Development probe.
public enum CloudSyncDevelopmentProbe {
    public static let zoneName = "QuizzlerDevelopmentProbe-v1"
    public static let recordType = "DevelopmentProbe"
    public static let recordName = "lifecycle"
    public static let maximumRecordsPerBatch = 400
}

/// Progress is deliberately free of record contents and account identifiers.
public enum CloudSyncProbeProgress: String, Sendable, Equatable {
    case disabled
    case checkingAccount
    case savingZone
    case savingRecord
    case fetchingForConflict
    case replayingAfterConflict
    case deletingRecord
    case deletingZone
    case complete
    case failed
}

/// A fail-visible result for an attended Development probe.
public struct CloudSyncProbeResult: Sendable, Equatable {
    public let progress: CloudSyncProbeProgress
    public let status: String

    public init(progress: CloudSyncProbeProgress, status: String) {
        self.progress = progress
        self.status = status
    }
}

public enum CloudSyncProbeError: Error, Sendable, Equatable {
    /// Callers must explicitly opt into a live Development mutation.
    case explicitOptInRequired
    /// The signed app lacks CloudKit/remote-notification capability, or no
    /// iCloud account is available. The underlying error is intentionally not
    /// exposed in public evidence.
    case unavailableEntitlementOrAccount
    case unsupportedPlatform
}

public enum CloudSyncPendingOperation: String, Codable, Sendable, Equatable {
    case save
    case delete
}

/// An operation contains only a stable record name and public payload digest.
/// Record values and account identifiers never enter this boundary.
public struct CloudSyncPendingChange: Codable, Sendable, Equatable, Hashable {
    public let recordName: String
    public let operation: CloudSyncPendingOperation
    public let revision: Int
    public let payloadHash: String?

    public init(
        recordName: String,
        operation: CloudSyncPendingOperation,
        revision: Int,
        payloadHash: String? = nil
    ) {
        self.recordName = recordName
        self.operation = operation
        self.revision = revision
        self.payloadHash = payloadHash
    }
}

/// Durable local intent used to recover from a process exit between an engine
/// state update and a delete acknowledgement. Latest intent wins per record.
public struct CloudSyncProbeLedger: Codable, Sendable, Equatable {
    public private(set) var pendingChanges: [CloudSyncPendingChange]
    public private(set) var serverRevision: Int

    public init(pendingChanges: [CloudSyncPendingChange] = [], serverRevision: Int = 0) {
        self.pendingChanges = []
        self.serverRevision = serverRevision
        for change in pendingChanges {
            enqueue(change)
        }
    }

    /// Compact repeated changes to the same record before they reach
    /// CKSyncEngine. This gives crash recovery a single durable intent.
    public mutating func enqueue(_ change: CloudSyncPendingChange) {
        pendingChanges.removeAll { $0.recordName == change.recordName }
        pendingChanges.append(change)
    }

    public func batches(maximumSize: Int = CloudSyncDevelopmentProbe.maximumRecordsPerBatch) -> [[CloudSyncPendingChange]] {
        precondition(maximumSize > 0)
        return stride(from: 0, to: pendingChanges.count, by: maximumSize).map {
            Array(pendingChanges[$0..<min($0 + maximumSize, pendingChanges.count)])
        }
    }

    /// Persist this ledger before asking CKSyncEngine to send a deletion. On
    /// relaunch the same intent can be offered again even if engine state was
    /// not serialized before a crash.
    public func serialized() throws -> Data {
        try JSONEncoder().encode(self)
    }

    public static func restored(from data: Data) throws -> CloudSyncProbeLedger {
        try JSONDecoder().decode(CloudSyncProbeLedger.self, from: data)
    }

    /// A stale client preserves its local intent and replays at the current
    /// server revision, instead of overwriting with an unbased revision.
    public mutating func rebase(on serverRevision: Int) {
        guard serverRevision > self.serverRevision else { return }
        self.serverRevision = serverRevision
        pendingChanges = pendingChanges.map {
            CloudSyncPendingChange(
                recordName: $0.recordName,
                operation: $0.operation,
                revision: serverRevision + 1,
                payloadHash: $0.payloadHash
            )
        }
    }

    public mutating func acknowledge(_ changes: [CloudSyncPendingChange]) {
        let acknowledged = Set(changes)
        pendingChanges.removeAll { acknowledged.contains($0) }
    }
}
