import Foundation

#if canImport(CloudKit)
import CloudKit
#endif

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
    /// The probe never lets CKSyncEngine schedule work independently. Every
    /// mutation is initiated by an explicit fetch/send call.
    public static let automaticallySync = false
}

/// Progress is deliberately free of record contents and account identifiers.
public enum CloudSyncProbeProgress: String, Sendable, Equatable {
    case disabled
    case checkingAccount
    case savingZone
    case savingRecord
    case competingWrite
    case conflictDetected
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
    /// The attended probe bound elapsed. The request was cancelled and the
    /// caller returned without waiting for an uncooperative operation.
    case operationTimedOut
    /// The attending task was cancelled. The operation is cancelled and the
    /// caller returns immediately rather than waiting for a stalled request.
    case operationCancelled
    /// CKSyncEngine delivered a state update that could not be durably saved.
    /// The lifecycle must stop rather than report success with unrecoverable
    /// engine state.
    case statePersistenceFailed
    /// The lifecycle failed after the disposable zone may have been created,
    /// and its exact-name cleanup did not complete. Callers must retain this
    /// failure as evidence and must not broaden the deletion target.
    case disposableZoneCleanupFailed
    /// The explicit competing write did not receive a successful save result.
    case competingWriteFailed
    case accountStatusFailed
    case fetchChangesFailed
    case sendChangesFailed
    case savingZoneFailed
    case savingRecordFailed
    case conflictSendFailed
    case conflictFetchFailed
    case replaySendFailed
    case deletingRecordFailed
    case deletingZoneFailed
    /// The stale local write completed without CloudKit delivering a conflict.
    case conflictNotObserved
    /// The conflict replay completed without an acknowledged replay save.
    case replayNotAcknowledged
    /// The exact zone was deleted, but local engine state could not be removed.
    case stateResetFailed
    case unsupportedPlatform

    /// Stable, privacy-safe evidence status. Underlying CloudKit errors never
    /// cross the probe boundary.
    public var redactedStatus: String {
        switch self {
        case .explicitOptInRequired: return "explicit_opt_in_required"
        case .unavailableEntitlementOrAccount: return "unavailable_entitlement_or_account"
        case .operationTimedOut: return "operation_timed_out"
        case .operationCancelled: return "operation_cancelled"
        case .statePersistenceFailed: return "state_persistence_failed"
        case .disposableZoneCleanupFailed: return "disposable_zone_cleanup_failed"
        case .competingWriteFailed: return "competing_write_failed"
        case .accountStatusFailed: return "account_status_failed"
        case .fetchChangesFailed: return "fetch_changes_failed"
        case .sendChangesFailed: return "send_changes_failed"
        case .savingZoneFailed: return "saving_zone_failed"
        case .savingRecordFailed: return "saving_record_failed"
        case .conflictSendFailed: return "conflict_send_failed"
        case .conflictFetchFailed: return "conflict_fetch_failed"
        case .replaySendFailed: return "replay_send_failed"
        case .deletingRecordFailed: return "deleting_record_failed"
        case .deletingZoneFailed: return "deleting_zone_failed"
        case .conflictNotObserved: return "conflict_not_observed"
        case .replayNotAcknowledged: return "replay_not_acknowledged"
        case .stateResetFailed: return "state_reset_failed"
        case .unsupportedPlatform: return "unsupported_platform"
        }
    }
}

#if canImport(CloudKit)
/// Matches CloudKit errors after async task-group boundaries have bridged them
/// to NSError. Only the documented domain and numeric code are inspected.
func cloudSyncProbeIsServerRecordChanged(_ error: Error) -> Bool {
    let nsError = error as NSError
    func isDirectServerRecordChanged(_ error: NSError) -> Bool {
        error.domain == CKErrorDomain
            && error.code == CKError.Code.serverRecordChanged.rawValue
    }

    if isDirectServerRecordChanged(nsError) {
        return true
    }

    guard nsError.domain == CKErrorDomain,
          nsError.code == CKError.Code.partialFailure.rawValue,
          let partialErrors = nsError.userInfo[CKPartialErrorsByItemIDKey] as? NSDictionary else {
        return false
    }

    return partialErrors.allValues.contains { value in
        guard let nestedError = value as? Error else { return false }
        return isDirectServerRecordChanged(nestedError as NSError)
    }
}

/// Identifies the CloudKit account/capability failures that are safe to map
/// to the single redacted availability status exposed by the probe.
func cloudSyncProbeIsEntitlementOrAccount(_ error: Error) -> Bool {
    let nsError = error as NSError
    guard nsError.domain == CKErrorDomain else { return false }
    return [
        CKError.Code.missingEntitlement.rawValue,
        CKError.Code.notAuthenticated.rawValue,
        CKError.Code.accountTemporarilyUnavailable.rawValue
    ].contains(nsError.code)
}
#endif

struct CloudSyncProbePersistenceGate: Sendable {
    private(set) var failed = false
    private(set) var successfulPersistenceCount = 0
    private var terminalAcknowledgementPersistenceCount: Int?

    mutating func markPersisted() {
        successfulPersistenceCount += 1
    }

    mutating func markFailed() {
        failed = true
    }

    func throwIfFailed() throws {
        if failed { throw CloudSyncProbeError.statePersistenceFailed }
    }

    /// A terminal lifecycle cannot reuse a serialization that preceded the
    /// exact CloudKit delete acknowledgement. Call this from that acknowledged
    /// database-change event, then require one newer successful persistence.
    mutating func markTerminalDeletionAcknowledged() {
        terminalAcknowledgementPersistenceCount = successfulPersistenceCount
    }

    func throwIfNotReadyForCompletion() throws {
        guard let terminalAcknowledgementPersistenceCount,
              !failed,
              successfulPersistenceCount > terminalAcknowledgementPersistenceCount else {
            throw CloudSyncProbeError.statePersistenceFailed
        }
    }
}

/// Tracks the acknowledgement for one exact disposable-zone deletion.
/// CloudKit reports database-change failures separately from the async send
/// result, so a successful send alone is not sufficient evidence of deletion.
struct CloudSyncZoneDeletionAcknowledgement<Zone: Hashable & Sendable>: Sendable {
    enum State: Sendable, Equatable {
        case idle
        case pending
        case deleted
        case failed
    }

    private(set) var state: State = .idle
    private var expectedZone: Zone?

    mutating func begin(for zone: Zone) {
        expectedZone = zone
        state = .pending
    }

    mutating func receive(deletedZones: [Zone], failedZones: Set<Zone>) -> Bool {
        guard let expectedZone else { return false }
        guard failedZones.contains(expectedZone) || deletedZones.contains(expectedZone) else {
            return false
        }
        guard failedZones.isEmpty, deletedZones.count == 1, deletedZones[0] == expectedZone else {
            state = .failed
            return false
        }
        state = .deleted
        return true
    }

    func throwIfDeleted(for zone: Zone) throws {
        guard expectedZone == zone, state == .deleted else {
            throw CloudSyncProbeError.disposableZoneCleanupFailed
        }
    }
}

/// Runs one sync operation with a hard caller-return bound. CloudKit work is
/// cancelled on timeout/cancellation but is never awaited after the terminal
/// status is surfaced; a later request can use the exact-zone recovery path.
func cloudSyncProbeBounded<T: Sendable>(
    timeout: Duration,
    operation: @escaping @Sendable () async throws -> T,
    onTimeout: @escaping @Sendable () -> Void,
    onCancellation: @escaping @Sendable () -> Void = {}
) async throws -> T {
    let control = CloudSyncProbeBoundedControl<T>(
        onTimeout: onTimeout,
        onCancellation: onCancellation
    )
    return try await withTaskCancellationHandler(operation: {
        try await withCheckedThrowingContinuation { continuation in
            control.start(operation: operation, timeout: timeout, continuation: continuation)
        }
    }, onCancel: {
        control.cancel()
    })
}

private final class CloudSyncProbeBoundedControl<Value: Sendable>: @unchecked Sendable {
    private let lock = NSLock()
    private let onTimeout: @Sendable () -> Void
    private let onCancellation: @Sendable () -> Void
    private var continuation: CheckedContinuation<Value, Error>?
    private var operationTask: Task<Void, Never>?
    private var cancellationRequested = false

    init(
        onTimeout: @escaping @Sendable () -> Void,
        onCancellation: @escaping @Sendable () -> Void
    ) {
        self.onTimeout = onTimeout
        self.onCancellation = onCancellation
    }

    func start(
        operation: @escaping @Sendable () async throws -> Value,
        timeout: Duration,
        continuation: CheckedContinuation<Value, Error>
    ) {
        let cancelBeforeStart = lock.withLock { () -> Bool in
            guard !cancellationRequested else { return true }
            self.continuation = continuation
            return false
        }
        if cancelBeforeStart {
            onCancellation()
            continuation.resume(throwing: CloudSyncProbeError.operationCancelled)
            return
        }
        let task = Task { [weak self] in
            let result: Result<Value, Error>
            do {
                let value: Value = try await operation()
                result = .success(value)
            } catch {
                result = .failure(error)
            }
            self?.finish(result)
        }
        let cancelImmediately = lock.withLock { () -> Bool in
            operationTask = task
            return self.continuation == nil
        }
        if cancelImmediately { task.cancel() }
        Task { [weak self, task] in
            do {
                try await Task.sleep(for: timeout)
            } catch {
                return
            }
            self?.timeout(task)
        }
    }

    func cancel() {
        let state = lock.withLock { () -> (CheckedContinuation<Value, Error>?, Task<Void, Never>?) in
            cancellationRequested = true
            guard let continuation else { return (nil, operationTask) }
            self.continuation = nil
            return (continuation, operationTask)
        }
        guard let continuation = state.0 else { return }
        state.1?.cancel()
        onCancellation()
        continuation.resume(throwing: CloudSyncProbeError.operationCancelled)
    }

    private func timeout(_ task: Task<Void, Never>) {
        resolve(
            .failure(CloudSyncProbeError.operationTimedOut),
            notify: onTimeout,
            task: task,
            cancelOperation: true
        )
    }

    private func finish(_ result: Result<Value, Error>) {
        resolve(result)
    }

    private func resolve(
        _ result: Result<Value, Error>,
        notify: (() -> Void)? = nil,
        task: Task<Void, Never>? = nil,
        cancelOperation: Bool = false
    ) {
        let state = lock.withLock { () -> (CheckedContinuation<Value, Error>?, Task<Void, Never>?) in
            guard let continuation else { return (nil, nil) }
            self.continuation = nil
            return (continuation, task ?? self.operationTask)
        }
        guard let continuation = state.0 else { return }
        if cancelOperation { state.1?.cancel() }
        notify?()
        continuation.resume(with: result)
    }
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
