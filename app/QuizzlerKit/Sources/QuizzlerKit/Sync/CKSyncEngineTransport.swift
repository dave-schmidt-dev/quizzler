import Foundation

#if canImport(CloudKit)
import CloudKit

/// Persists CKSyncEngine's opaque state separately from the probe ledger.
/// Implementations must durably save every value passed to `save`.
public protocol CloudSyncEngineStatePersisting: Sendable {
    func load() throws -> Data?
    func save(_ data: Data) throws
}

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
}

/// CKSyncEngine adapter for a disposable private Development zone. Constructing
/// it is inert. `runDevelopmentLifecycle(explicitlyEnabled:)` is the only
/// method that schedules the save/replay/delete mutations.
@available(iOS 17.0, macOS 14.0, *)
public final class CKSyncEngineTransport: @unchecked Sendable, CloudSyncTransport {
    private let delegate: Delegate
    private let engine: CKSyncEngine
    private let zoneID: CKRecordZone.ID
    private let container: CKContainer

    public init(
        containerIdentifier: String,
        stateStore: any CloudSyncEngineStatePersisting,
        progress: @escaping @Sendable (CloudSyncProbeResult) -> Void = { _ in }
    ) throws {
        container = CKContainer(identifier: containerIdentifier)
        zoneID = CKRecordZone.ID(zoneName: CloudSyncDevelopmentProbe.zoneName)
        delegate = Delegate(stateStore: stateStore, progress: progress)

        let serialization = try stateStore.load().flatMap {
            try JSONDecoder().decode(CKSyncEngine.State.Serialization.self, from: $0)
        }
        var configuration = CKSyncEngine.Configuration(
            database: container.privateCloudDatabase,
            stateSerialization: serialization,
            delegate: delegate
        )
        // The engine may sync in the background after a live probe is enabled;
        // explicit fetch/send below remain the observable lifecycle boundary.
        configuration.automaticallySync = true
        engine = CKSyncEngine(configuration)
    }

    public func fetchChanges() async throws {
        do {
            try await engine.fetchChanges(.init(scope: .zoneIDs([zoneID])))
        } catch {
            throw CloudSyncProbeError.unavailableEntitlementOrAccount
        }
    }

    public func sendChanges() async throws {
        do {
            try await engine.sendChanges(.init(scope: .zoneIDs([zoneID])))
        } catch {
            throw CloudSyncProbeError.unavailableEntitlementOrAccount
        }
    }

    /// Executes a disposable private-zone lifecycle only when a human-visible
    /// caller passes `true`. It never targets the public database.
    public func runDevelopmentLifecycle(explicitlyEnabled: Bool) async throws -> CloudSyncProbeResult {
        guard explicitlyEnabled else { throw CloudSyncProbeError.explicitOptInRequired }
        report(.checkingAccount, status: "checking_account")
        do {
            guard try await container.accountStatus() == .available else {
                throw CloudSyncProbeError.unavailableEntitlementOrAccount
            }
        } catch let error as CloudSyncProbeError {
            throw error
        } catch {
            throw CloudSyncProbeError.unavailableEntitlementOrAccount
        }

        let recordID = CKRecord.ID(
            recordName: CloudSyncDevelopmentProbe.recordName,
            zoneID: zoneID
        )
        let record = CKRecord(recordType: CloudSyncDevelopmentProbe.recordType, recordID: recordID)
        record["status"] = "created" as CKRecordValue

        report(.savingZone, status: "saving_zone")
        engine.state.add(pendingDatabaseChanges: [.saveZone(CKRecordZone(zoneID: zoneID))])
        try await sendChanges()

        report(.savingRecord, status: "saving_record")
        delegate.setRecord(record)
        engine.state.add(pendingRecordZoneChanges: [.saveRecord(recordID)])
        try await sendChanges()

        // An explicit fetch makes a conflicting server change visible before
        // replay. The delegate reoffers the latest local record on send.
        report(.fetchingForConflict, status: "fetching_for_conflict")
        try await fetchChanges()
        report(.replayingAfterConflict, status: "replaying_after_conflict")
        record["status"] = "replayed" as CKRecordValue
        delegate.setRecord(record)
        engine.state.add(pendingRecordZoneChanges: [.saveRecord(recordID)])
        try await sendChanges()

        report(.deletingRecord, status: "deleting_record")
        delegate.removeRecord(recordID)
        engine.state.add(pendingRecordZoneChanges: [.deleteRecord(recordID)])
        try await sendChanges()

        report(.deletingZone, status: "deleting_zone")
        engine.state.add(pendingDatabaseChanges: [.deleteZone(zoneID)])
        try await sendChanges()

        let result = CloudSyncProbeResult(progress: .complete, status: "complete")
        report(result.progress, status: result.status)
        return result
    }

    private func report(_ progress: CloudSyncProbeProgress, status: String) {
        delegate.report(CloudSyncProbeResult(progress: progress, status: status))
    }

    private final class Delegate: NSObject, CKSyncEngineDelegate, @unchecked Sendable {
        private let stateStore: any CloudSyncEngineStatePersisting
        private let progress: @Sendable (CloudSyncProbeResult) -> Void
        private let lock = NSLock()
        private var records: [CKRecord.ID: CKRecord] = [:]

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
                } catch {
                    report(CloudSyncProbeResult(progress: .failed, status: "state_persist_failed"))
                }
            }
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

        func removeRecord(_ recordID: CKRecord.ID) {
            _ = lock.withLock { records.removeValue(forKey: recordID) }
        }

        func record(for recordID: CKRecord.ID) -> CKRecord? {
            lock.withLock { records[recordID] }
        }

        func report(_ result: CloudSyncProbeResult) {
            progress(result)
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
