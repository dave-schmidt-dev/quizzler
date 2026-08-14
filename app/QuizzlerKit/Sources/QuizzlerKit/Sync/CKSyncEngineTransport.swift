import Foundation

#if canImport(CloudKit)
import CloudKit

/// Persists CKSyncEngine's opaque state separately from the probe ledger.
/// Implementations must durably save every value passed to `save`.
public protocol CloudSyncEngineStatePersisting: Sendable {
    func load() throws -> Data?
    func save(_ data: Data) throws
}

#if DEBUG
/// Opt-in capability for the recovery operation to remove the local engine
/// state after CloudKit confirms deletion of the exact probe zone.
public protocol CloudSyncEngineStateClearing: Sendable {
    func clear() throws
}
#endif

/// File-backed state persistence is intentionally injected by the app. This
/// object is small so tests can supply an in-memory implementation.
public final class CloudSyncEngineStateStore: @unchecked Sendable, CloudSyncEngineStatePersisting {
    private let url: URL
    private let fileManager: FileManager

    public init(url: URL, fileManager: FileManager = .default) {
        self.url = url
        self.fileManager = fileManager
    }

    public func load() throws -> Data? {
        guard fileManager.fileExists(atPath: url.path) else { return nil }
        return try Data(contentsOf: url)
    }

    public func save(_ data: Data) throws {
        try data.write(to: url, options: .atomic)
    }

#if DEBUG
    public func clear() throws {
        guard fileManager.fileExists(atPath: url.path) else { return }
        try fileManager.removeItem(at: url)
    }
#endif
}

#if DEBUG
extension CloudSyncEngineStateStore: CloudSyncEngineStateClearing {}
#endif

/// CKSyncEngine adapter for a disposable private Development zone. Constructing
/// it is inert. `runDevelopmentLifecycle(explicitlyEnabled:)` is the only
/// method that schedules the save/replay/delete mutations.
@available(iOS 17.0, macOS 14.0, *)
public final class CKSyncEngineTransport: @unchecked Sendable, CloudSyncTransport {
    private static let operationCancellationBound: Duration = .seconds(30)
    private let delegate: Delegate
    private let engine: CKSyncEngine
    private let zoneID: CKRecordZone.ID
    private let container: CKContainer

    public convenience init(
        containerIdentifier: String,
        stateStore: any CloudSyncEngineStatePersisting,
        progress: @escaping @Sendable (CloudSyncProbeResult) -> Void = { _ in }
    ) throws {
        try self.init(
            containerIdentifier: containerIdentifier,
            stateStore: stateStore,
            progress: progress,
            restorePersistedState: true
        )
    }

    private init(
        containerIdentifier: String,
        stateStore: any CloudSyncEngineStatePersisting,
        progress: @escaping @Sendable (CloudSyncProbeResult) -> Void,
        restorePersistedState: Bool
    ) throws {
        container = CKContainer(identifier: containerIdentifier)
        zoneID = CKRecordZone.ID(zoneName: CloudSyncDevelopmentProbe.zoneName)
        delegate = Delegate(stateStore: stateStore, progress: progress)

        let serialization = restorePersistedState
            ? try stateStore.load().flatMap {
                try JSONDecoder().decode(CKSyncEngine.State.Serialization.self, from: $0)
            }
            : nil
        var configuration = CKSyncEngine.Configuration(
            database: container.privateCloudDatabase,
            stateSerialization: serialization,
            delegate: delegate
        )
        // The probe is deliberately explicit: constructing it or restoring
        // state cannot schedule a background mutation.
        configuration.automaticallySync = CloudSyncDevelopmentProbe.automaticallySync
        engine = CKSyncEngine(configuration)
    }

    public func fetchChanges() async throws {
        try await fetchChanges(failure: .fetchChangesFailed)
    }

    private func fetchChanges(failure: CloudSyncProbeError) async throws {
        do {
            try await bounded { [self] in
                try await engine.fetchChanges(.init(scope: .zoneIDs([zoneID])))
            }
        } catch let error as CloudSyncProbeError {
            throw error
        } catch where cloudSyncProbeIsEntitlementOrAccount(error) {
            throw CloudSyncProbeError.unavailableEntitlementOrAccount
        } catch {
            throw failure
        }
    }

    public func sendChanges() async throws {
        try await sendChanges(failure: .sendChangesFailed)
    }

    private func sendChanges(
        failure: CloudSyncProbeError,
        allowingServerRecordChanged: Bool = false
    ) async throws {
        do {
            try await bounded { [self] in
                try await engine.sendChanges(.init(scope: .zoneIDs([zoneID])))
            }
        } catch let error as CloudSyncProbeError {
            throw error
        } catch where allowingServerRecordChanged && cloudSyncProbeIsServerRecordChanged(error) {
            return
        } catch where cloudSyncProbeIsEntitlementOrAccount(error) {
            throw CloudSyncProbeError.unavailableEntitlementOrAccount
        } catch {
            throw failure
        }
    }

    /// Executes a disposable private-zone lifecycle only when a human-visible
    /// caller passes `true`. It never targets the public database.
    public func runDevelopmentLifecycle(explicitlyEnabled: Bool) async throws -> CloudSyncProbeResult {
        guard explicitlyEnabled else { throw CloudSyncProbeError.explicitOptInRequired }
        report(.checkingAccount, status: "checking_account")
        do {
            guard try await bounded({ [container] in try await container.accountStatus() }) == .available else {
                throw CloudSyncProbeError.unavailableEntitlementOrAccount
            }
        } catch let error as CloudSyncProbeError {
            throw error
        } catch where cloudSyncProbeIsEntitlementOrAccount(error) {
            throw CloudSyncProbeError.unavailableEntitlementOrAccount
        } catch {
            throw CloudSyncProbeError.accountStatusFailed
        }

        let recordID = CKRecord.ID(
            recordName: CloudSyncDevelopmentProbe.recordName,
            zoneID: zoneID
        )
        let record = CKRecord(recordType: CloudSyncDevelopmentProbe.recordType, recordID: recordID)
        record["status"] = "created" as CKRecordValue

        var zoneMayExist = false
        do {
            report(.savingZone, status: "saving_zone")
            // Once this exact zone is queued, any later failure must attempt
            // cleanup of this ID only. No record-zone enumeration is allowed.
            zoneMayExist = true
            engine.state.add(pendingDatabaseChanges: [.saveZone(CKRecordZone(zoneID: zoneID))])
            try await sendChanges(failure: .savingZoneFailed)
            try delegate.throwIfPersistenceFailed()

            report(.savingRecord, status: "saving_record")
            delegate.setRecord(record)
            engine.state.add(pendingRecordZoneChanges: [.saveRecord(recordID)])
            try await sendChanges(failure: .savingRecordFailed)
            try delegate.throwIfPersistenceFailed()

            // Read a server-tagged base, then use a separate write based on the
            // same tag to create a real competing version in this exact zone.
            // The stale base is subsequently sent through CKSyncEngine so the
            // serverRecordChanged path is exercised rather than simulated.
            let staleBase = try await bounded { [container, recordID] in
                try await container.privateCloudDatabase.record(for: recordID)
            }
            report(.competingWrite, status: "competing_write")
            try await saveCompetingRecord(basedOn: staleBase)

            let staleLocalRecord = staleBase.copy() as! CKRecord
            staleLocalRecord["status"] = "local_stale" as CKRecordValue
            delegate.setRecord(staleLocalRecord)
            engine.state.add(pendingRecordZoneChanges: [.saveRecord(recordID)])
            try await sendChanges(failure: .conflictSendFailed, allowingServerRecordChanged: true)
            try delegate.throwIfPersistenceFailed()
            guard delegate.consumeConflict(for: recordID) else {
                throw CloudSyncProbeError.conflictNotObserved
            }

            // An explicit fetch makes the competing server version visible
            // before the delegate's server-record merge is replayed.
            report(.fetchingForConflict, status: "fetching_for_conflict")
            try await fetchChanges(failure: .conflictFetchFailed)
            try delegate.throwIfPersistenceFailed()
            report(.replayingAfterConflict, status: "replaying_after_conflict")
            try await sendChanges(failure: .replaySendFailed)
            try delegate.throwIfPersistenceFailed()
            guard delegate.consumeReplay(for: recordID) else {
                throw CloudSyncProbeError.replayNotAcknowledged
            }

            report(.deletingRecord, status: "deleting_record")
            delegate.removeRecord(recordID)
            engine.state.add(pendingRecordZoneChanges: [.deleteRecord(recordID)])
            try await sendChanges(failure: .deletingRecordFailed)
            try delegate.throwIfPersistenceFailed()

            try await deleteDisposableZone()
            zoneMayExist = false
            try await bounded { [delegate] in
                try await delegate.awaitTerminalStatePersistence()
            }
            try delegate.throwIfReadyForCompletion()
            let result = CloudSyncProbeResult(progress: .complete, status: "complete")
            report(result.progress, status: result.status)
            return result
        } catch {
            // A timeout has already surfaced a terminal status. Do not begin
            // another potentially stalled cleanup request after that visible
            // terminal state; the DEBUG recovery path only deletes this exact
            // zone on the next attended attempt.
            if let probeError = error as? CloudSyncProbeError,
               probeError == .operationTimedOut || probeError == .operationCancelled {
                throw probeError
            }
            if zoneMayExist {
                do {
                    try await deleteDisposableZone()
                } catch {
                    report(.failed, status: "disposable_zone_cleanup_failed")
                    throw CloudSyncProbeError.disposableZoneCleanupFailed
                }
            }
            if let error = error as? CloudSyncProbeError {
                report(.failed, status: error.redactedStatus)
            } else {
                report(.failed, status: "probe_failed")
            }
            throw error
        }
    }

#if DEBUG
    /// Explicitly removes the exact Development probe zone, then clears local
    /// state only after CloudKit acknowledges that exact deletion. This
    /// operation is unavailable to Release builds and never constructs an
    /// engine, queues pending mutations, enumerates zones, or touches records.
    public static func recoverDevelopmentProbe(
        explicitlyEnabled: Bool,
        containerIdentifier: String,
        stateStore: any CloudSyncEngineStatePersisting,
        progress: @escaping @Sendable (CloudSyncProbeResult) -> Void = { _ in }
    ) async throws -> CloudSyncProbeResult {
        guard explicitlyEnabled else { throw CloudSyncProbeError.explicitOptInRequired }
        guard let clearingStore = stateStore as? any CloudSyncEngineStateClearing else {
            throw CloudSyncProbeError.stateResetFailed
        }
        let container = CKContainer(identifier: containerIdentifier)
        let zoneID = CKRecordZone.ID(zoneName: CloudSyncDevelopmentProbe.zoneName)
        let report: @Sendable (CloudSyncProbeResult) -> Void = progress
        report(CloudSyncProbeResult(progress: .checkingAccount, status: "recovery_checking_account"))
        do {
            report(CloudSyncProbeResult(progress: .deletingZone, status: "deleting_zone"))
            _ = try await cloudSyncProbeBounded(
                timeout: .seconds(30),
                operation: { [container, zoneID] in
                    try await container.privateCloudDatabase.deleteRecordZone(withID: zoneID)
                },
                onTimeout: {
                    report(CloudSyncProbeResult(progress: .failed, status: "operation_timed_out"))
                }
            )
            // This is deliberately after the exact-zone acknowledgement.
            try clearingStore.clear()
            let result = CloudSyncProbeResult(progress: .complete, status: "recovery_complete")
            report(result)
            return result
        } catch let error as CloudSyncProbeError {
            report(CloudSyncProbeResult(progress: .failed, status: error.redactedStatus))
            throw error
        } catch {
            report(CloudSyncProbeResult(progress: .failed, status: "disposable_zone_cleanup_failed"))
            throw CloudSyncProbeError.disposableZoneCleanupFailed
        }
    }
#endif

    private func deleteDisposableZone() async throws {
        report(.deletingZone, status: "deleting_zone")
        delegate.beginZoneDeletion(zoneID)
        let pendingRecordChanges = engine.state.pendingRecordZoneChanges.filter { change in
            switch change {
            case let .saveRecord(recordID), let .deleteRecord(recordID):
                return recordID.zoneID == zoneID
            @unknown default:
                return false
            }
        }
        // A zone delete is the terminal cleanup operation. Remove only this
        // zone's pending record changes so they cannot be sent alongside or
        // after the exact database-zone deletion.
        engine.state.remove(pendingRecordZoneChanges: pendingRecordChanges)
        engine.state.add(pendingDatabaseChanges: [.deleteZone(zoneID)])
        try await sendChanges(failure: .deletingZoneFailed)
        try delegate.throwIfZoneDeletionWasAcknowledged(zoneID)
    }

    private func saveCompetingRecord(basedOn staleBase: CKRecord) async throws {
        let competing = staleBase.copy() as! CKRecord
        competing["status"] = "competing" as CKRecordValue
        let result = try await bounded { [container, competing] in
            try await container.privateCloudDatabase.modifyRecords(
                saving: [competing],
                deleting: [],
                savePolicy: .ifServerRecordUnchanged,
                atomically: true
            )
        }
        guard case .success = result.saveResults[competing.recordID] else {
            throw CloudSyncProbeError.competingWriteFailed
        }
    }

    private func bounded<T: Sendable>(
        _ operation: @escaping @Sendable () async throws -> T
    ) async throws -> T {
        // `cloudSyncProbeBounded` returns immediately after it publishes a
        // timeout/cancellation terminal state. It cancels an uncooperative
        // request and leaves exact-zone recovery to the explicit DEBUG route.
        try await cloudSyncProbeBounded(
            timeout: Self.operationCancellationBound,
            operation: operation,
            onTimeout: { [weak self] in
                self?.report(.failed, status: "operation_timed_out")
            },
            onCancellation: { [weak self] in
                self?.report(.failed, status: "operation_cancelled")
            }
        )
    }

    private func report(_ progress: CloudSyncProbeProgress, status: String) {
        delegate.report(CloudSyncProbeResult(progress: progress, status: status))
    }

    final class Delegate: NSObject, CKSyncEngineDelegate, @unchecked Sendable {
        private let stateStore: any CloudSyncEngineStatePersisting
        private let progress: @Sendable (CloudSyncProbeResult) -> Void
        private let lock = NSLock()
        private var records: [CKRecord.ID: CKRecord] = [:]
        private var persistenceGate = CloudSyncProbePersistenceGate()
        private var terminalPersistenceWaiters: [UUID: CheckedContinuation<Void, Error>] = [:]
        private var cancelledTerminalPersistenceWaiters: Set<UUID> = []
        private var zoneDeletionAcknowledgement = CloudSyncZoneDeletionAcknowledgement<CKRecordZone.ID>()
        private var conflictedRecordIDs: Set<CKRecord.ID> = []
        private var replayedRecordIDs: Set<CKRecord.ID> = []

        init(
            stateStore: any CloudSyncEngineStatePersisting,
            progress: @escaping @Sendable (CloudSyncProbeResult) -> Void
        ) {
            self.stateStore = stateStore
            self.progress = progress
        }

        func handleEvent(_ event: CKSyncEngine.Event, syncEngine: CKSyncEngine) async {
            if case let .stateUpdate(update) = event {
                do {
                    // This is mandatory on every state-update event so a
                    // relaunch retains subscriptions and pending changes.
                    try stateStore.save(JSONEncoder().encode(update.stateSerialization))
                    let waiters = lock.withLock { () -> [CheckedContinuation<Void, Error>] in
                        persistenceGate.markPersisted()
                        guard (try? persistenceGate.throwIfNotReadyForCompletion()) != nil else {
                            return []
                        }
                        let waiters = Array(terminalPersistenceWaiters.values)
                        terminalPersistenceWaiters.removeAll()
                        return waiters
                    }
                    waiters.forEach { $0.resume() }
                } catch {
                    let waiters = lock.withLock { () -> [CheckedContinuation<Void, Error>] in
                        persistenceGate.markFailed()
                        let waiters = Array(terminalPersistenceWaiters.values)
                        terminalPersistenceWaiters.removeAll()
                        return waiters
                    }
                    waiters.forEach { $0.resume(throwing: CloudSyncProbeError.statePersistenceFailed) }
                    report(CloudSyncProbeResult(progress: .failed, status: "state_persist_failed"))
                }
            }
            if case let .sentDatabaseChanges(changes) = event {
                handleSentDatabaseChanges(changes)
            }
            if case let .sentRecordZoneChanges(changes) = event {
                handleSentRecordZoneChanges(changes, syncEngine: syncEngine)
            }
        }

        func throwIfPersistenceFailed() throws {
            let failed = lock.withLock { persistenceGate.failed }
            if failed { throw CloudSyncProbeError.statePersistenceFailed }
        }

        func throwIfReadyForCompletion() throws {
            try lock.withLock { try persistenceGate.throwIfNotReadyForCompletion() }
        }

        func beginZoneDeletion(_ zoneID: CKRecordZone.ID) {
            lock.withLock { zoneDeletionAcknowledgement.begin(for: zoneID) }
        }

        func throwIfZoneDeletionWasAcknowledged(_ zoneID: CKRecordZone.ID) throws {
            try lock.withLock { try zoneDeletionAcknowledgement.throwIfDeleted(for: zoneID) }
        }

        func awaitTerminalStatePersistence() async throws {
            let waiterID = UUID()
            try await withTaskCancellationHandler(operation: {
                try await withCheckedThrowingContinuation { continuation in
                    let result = lock.withLock { () -> Result<Void, Error>? in
                        if cancelledTerminalPersistenceWaiters.remove(waiterID) != nil {
                            return .failure(CloudSyncProbeError.operationCancelled)
                        }
                        do {
                            try persistenceGate.throwIfFailed()
                            try persistenceGate.throwIfNotReadyForCompletion()
                            return .success(())
                        } catch let error as CloudSyncProbeError {
                            if error == .statePersistenceFailed,
                               !persistenceGate.failed {
                                terminalPersistenceWaiters[waiterID] = continuation
                                return nil
                            }
                            return .failure(error)
                        } catch {
                            return .failure(CloudSyncProbeError.statePersistenceFailed)
                        }
                    }
                    if let result { continuation.resume(with: result) }
                }
            }, onCancel: {
                self.cancelTerminalPersistenceWaiter(waiterID)
            })
        }

        private func cancelTerminalPersistenceWaiter(_ waiterID: UUID) {
            let continuation = lock.withLock { () -> CheckedContinuation<Void, Error>? in
                cancelledTerminalPersistenceWaiters.insert(waiterID)
                return terminalPersistenceWaiters.removeValue(forKey: waiterID)
            }
            continuation?.resume(throwing: CloudSyncProbeError.operationCancelled)
        }

        func nextRecordZoneChangeBatch(
            _ context: CKSyncEngine.SendChangesContext,
            syncEngine: CKSyncEngine
        ) async -> CKSyncEngine.RecordZoneChangeBatch? {
            let changes = syncEngine.state.pendingRecordZoneChanges.filter { context.options.scope.contains($0) }
            guard !changes.isEmpty else { return nil }
            let batchChanges = Array(changes.prefix(CloudSyncDevelopmentProbe.maximumRecordsPerBatch))
            return await CKSyncEngine.RecordZoneChangeBatch(pendingChanges: batchChanges) { [weak self] recordID in
                self?.record(for: recordID)
            }
        }

        func nextFetchChangesOptions(
            _ context: CKSyncEngine.FetchChangesContext,
            syncEngine: CKSyncEngine
        ) async -> CKSyncEngine.FetchChangesOptions {
            context.options
        }

        func setRecord(_ record: CKRecord) {
            lock.withLock { records[record.recordID] = record }
        }

        func consumeConflict(for recordID: CKRecord.ID) -> Bool {
            lock.withLock { conflictedRecordIDs.remove(recordID) != nil }
        }

        func consumeReplay(for recordID: CKRecord.ID) -> Bool {
            lock.withLock { replayedRecordIDs.remove(recordID) != nil }
        }

        func removeRecord(_ recordID: CKRecord.ID) {
            _ = lock.withLock { records.removeValue(forKey: recordID) }
        }

        func record(for recordID: CKRecord.ID) -> CKRecord? {
            lock.withLock { records[recordID] }
        }

        func report(_ result: CloudSyncProbeResult) {
            progress(result)
        }

        func handleSentDatabaseChanges(_ event: CKSyncEngine.Event.SentDatabaseChanges) {
            lock.withLock {
                let terminalDeleteAcknowledged = zoneDeletionAcknowledgement.receive(
                    deletedZones: event.deletedZoneIDs,
                    failedZones: Set(event.failedZoneDeletes.keys)
                )
                if terminalDeleteAcknowledged {
                    persistenceGate.markTerminalDeletionAcknowledged()
                }
            }
        }

        func handleSentRecordZoneChanges(_ event: CKSyncEngine.Event.SentRecordZoneChanges, syncEngine: CKSyncEngine) {
            for savedRecord in event.savedRecords {
                lock.withLock {
                    records[savedRecord.recordID] = savedRecord
                    if (savedRecord["status"] as? String) == "replayed" {
                        replayedRecordIDs.insert(savedRecord.recordID)
                    }
                }
            }
            for failure in event.failedRecordSaves where cloudSyncProbeIsServerRecordChanged(failure.error) {
                guard let serverRecord = failure.error.userInfo[CKRecordChangedErrorServerRecordKey] as? CKRecord else {
                    continue
                }
                let replayRecord = serverRecord.copy() as! CKRecord
                replayRecord["status"] = "replayed" as CKRecordValue
                lock.withLock {
                    records[failure.record.recordID] = replayRecord
                    conflictedRecordIDs.insert(failure.record.recordID)
                }
                syncEngine.state.add(pendingRecordZoneChanges: [.saveRecord(failure.record.recordID)])
                report(CloudSyncProbeResult(progress: .conflictDetected, status: "conflict_detected"))
            }
        }
    }
}

#else

/// Keeps non-Apple builds fail-visible rather than silently dropping a probe.
public struct CKSyncEngineTransport: Sendable, CloudSyncTransport {
    public init(containerIdentifier: String, stateStore: Never) throws {
        throw CloudSyncProbeError.unsupportedPlatform
    }

    public func fetchChanges() async throws { throw CloudSyncProbeError.unsupportedPlatform }
    public func sendChanges() async throws { throw CloudSyncProbeError.unsupportedPlatform }
}

#endif
