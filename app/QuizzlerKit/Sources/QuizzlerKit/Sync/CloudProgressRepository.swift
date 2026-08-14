import Foundation

#if canImport(CloudKit)
import CloudKit
#endif

public enum CloudProgressRepositoryError: Error, Equatable, Sendable {
    case corruptState
    case persistenceUnavailable
    case statePersistenceFailed
    case offline
    case accountUnavailable
    case tokenExpired
    case incompatibleVersion
    case malformedRecord
    case partialFailure
    case transportUnavailable
    case retryLimitReached
    case invalidOperation
    case rebaseRequired
    case accountIsolationRequired
    case issueQueueFull
    case encodedSizeRefused
}

public enum CloudProgressTransportError: Error, Codable, Equatable, Sendable {
    case offline
    case accountUnavailable
    case containerChanged
    case tokenExpired
    case network
    case serverRecordChanged
    case unavailable

    public var redactedStatus: String {
        switch self {
        case .offline: return "offline"
        case .accountUnavailable: return "account_unavailable"
        case .containerChanged: return "container_changed"
        case .tokenExpired: return "token_expired"
        case .network: return "network_failure"
        case .serverRecordChanged: return "server_record_changed"
        case .unavailable: return "transport_unavailable"
        }
    }
}

public enum CloudProgressRecordFailureReason: String, Codable, Sendable, Equatable {
    case serverRecordChanged = "server_record_changed"
    case permissionDenied = "permission_denied"
    case invalidArguments = "invalid_arguments"
    case network
    case unknown
}

/// The only failure target that may cross into durable repository state. The
/// remote record name itself is transport detail and is never persisted or
/// emitted as user status.
public enum CloudProgressFailureTarget: String, Codable, Sendable, Equatable {
    case operation
    case issue
    case snapshot
    case unknown
}

/// A per-record failure intentionally keeps the record name internal to sync
/// handling. Typed status events expose only counts and a redacted reason.
public struct CloudProgressRecordFailure: Codable, Sendable, Equatable {
    public let recordName: String
    public let reason: CloudProgressRecordFailureReason
    public let retryable: Bool

    public init(recordName: String, reason: CloudProgressRecordFailureReason, retryable: Bool) {
        self.recordName = recordName
        self.reason = reason
        self.retryable = retryable
    }

    public var target: CloudProgressFailureTarget {
        if recordName == CloudKitContract.snapshotRecordName { return .snapshot }
        if recordName.hasPrefix("\(CloudKitRecordKind.operation.rawValue)/") { return .operation }
        if recordName.hasPrefix("\(CloudKitRecordKind.issue.rawValue)/") { return .issue }
        return .unknown
    }
}

public struct CloudProgressFetchResult: Sendable, Equatable {
    public let records: [CloudKitMappedRecord]
    public let tokenExpired: Bool
    public let isFullSnapshot: Bool
    /// The raw authoritative snapshot's change tag, retained by the
    /// transport so an optimistic write can use the exact fetched version.
    public let snapshotChangeTag: String?

    public init(
        records: [CloudKitMappedRecord] = [],
        tokenExpired: Bool = false,
        snapshotChangeTag: String? = nil,
        isFullSnapshot: Bool = false
    ) {
        self.records = records
        self.tokenExpired = tokenExpired
        self.snapshotChangeTag = snapshotChangeTag
        self.isFullSnapshot = isFullSnapshot
    }
}

public struct CloudProgressSendResult: Sendable, Equatable {
    public let savedRecordNames: [String]
    public let deletedRecordNames: [String]
    public let failedRecords: [CloudProgressRecordFailure]
    public let serverRecords: [CloudKitMappedRecord]
    public let snapshotChangeTag: String?
    /// Revisions assigned by the authoritative snapshot CAS. These values
    /// are never synthesized by the client; the repository applies them only
    /// after the atomic save succeeds.
    public let assignedRevisions: [String: Int]

    public init(
        savedRecordNames: [String] = [],
        deletedRecordNames: [String] = [],
        failedRecords: [CloudProgressRecordFailure] = [],
        serverRecords: [CloudKitMappedRecord] = [],
        snapshotChangeTag: String? = nil,
        assignedRevisions: [String: Int] = [:]
    ) {
        self.savedRecordNames = savedRecordNames
        self.deletedRecordNames = deletedRecordNames
        self.failedRecords = failedRecords
        self.serverRecords = serverRecords
        self.snapshotChangeTag = snapshotChangeTag
        self.assignedRevisions = assignedRevisions
    }
}

/// The transport is deliberately narrower than CKSyncEngine. Its delegate
/// callback only delivers events; fetch/send are always initiated by the
/// repository's explicit methods, never from a delegate event.
public protocol CloudProgressTransport: Sendable {
    func fetchChanges() async throws -> CloudProgressFetchResult
    func fetchChanges(full: Bool) async throws -> CloudProgressFetchResult
    func sendChanges(_ records: [CloudKitMappedRecord]) async throws -> CloudProgressSendResult
    /// Atomically publishes the authoritative snapshot proposal with its
    /// operation records. Implementations must use the supplied expected
    /// revision/change tag as an optimistic compare-and-swap boundary.
    func sendProgressAtomically(
        _ records: [CloudKitMappedRecord],
        expectedRevision: Int,
        snapshotChangeTag: String?
    ) async throws -> CloudProgressSendResult
    func sendIssuesAtomically(_ records: [CloudKitMappedRecord]) async throws -> CloudProgressSendResult
    /// Sends durable compaction deletes. The default keeps older fakes and
    /// transports source-compatible while making a missing delete path fail
    /// closed when compaction actually needs it.
    func deleteChanges(_ recordNames: [String]) async throws -> CloudProgressSendResult
    func resetPendingChanges() async
}

public extension CloudProgressTransport {
    func fetchChanges(full: Bool) async throws -> CloudProgressFetchResult {
        try await fetchChanges()
    }
    func deleteChanges(_ recordNames: [String]) async throws -> CloudProgressSendResult {
        throw CloudProgressTransportError.unavailable
    }

    func sendProgressAtomically(
        _ records: [CloudKitMappedRecord],
        expectedRevision: Int,
        snapshotChangeTag: String?
    ) async throws -> CloudProgressSendResult {
        throw CloudProgressTransportError.unavailable
    }

    func sendIssuesAtomically(_ records: [CloudKitMappedRecord]) async throws -> CloudProgressSendResult {
        throw CloudProgressTransportError.unavailable
    }
}

public enum CloudProgressEngineEvent: Sendable, Equatable {
    case stateUpdate(Data)
    case accountChanged
    case containerChanged
    case tokenExpired
    case fetched([CloudKitMappedRecord])
    case sent([String])
    case deleted([String])
    case serverRecordChanged(CloudKitMappedRecord)
    case recordFailure(CloudProgressRecordFailure)
    case reachability(Bool)
}

/// The complete atomic checkpoint. A conforming persistence implementation
/// must replace this value as one file transaction so local progress and the
/// opaque CKSyncEngine state cannot acknowledge different generations.
public struct CloudProgressCheckpoint: Codable, Sendable, Equatable {
    public var envelope: ProgressEnvelope
    public var engineState: Data?
    public var changeToken: Data?
    public var snapshotChangeTag: String?
    public var remoteRecords: [CloudKitMappedRecord]
    public var sentOperationIDs: Set<String>
    public var sentIssueIDs: Set<String>
    public var snapshotDirty: Bool
    /// Recovery invalidates the engine cursor. Local progress remains durable,
    /// but no local snapshot may be sent until a successful fetch re-establishes
    /// the remote view.
    public var requiresRebase: Bool
    /// Account changes invalidate the ownership context of the local envelope.
    /// This gate is intentionally not cleared by a successful fetch.
    public var accountIsolationRequired: Bool
    /// Issue payloads remain in `envelope.issues` until their individual save
    /// is acknowledged. Failures retain only this redacted reason; transport
    /// record names and server error text never become durable state.
    public var failedIssueReasons: [String: CloudProgressRecordFailureReason]
    /// A remote full issue queue can temporarily leave a local report unable
    /// to fit in the shared snapshot. Preserve the local item and fail sends
    /// visibly instead of latching the repository in rebase-required state.
    public var issueQueueConflict: Bool
    public var lastFailureReason: CloudProgressRecordFailureReason?
    /// The deterministic merge state and staged recovery are part of the same
    /// atomic checkpoint as the envelope and CKSyncEngine state.
    public var mergeSnapshot: ProgressMergeSnapshot?
    public var recoveryCheckpoint: SyncRecoveryCheckpoint?
    public var pendingCompactionDeleteIDs: Set<String>

    public init(
        envelope: ProgressEnvelope,
        engineState: Data? = nil,
        changeToken: Data? = nil,
        snapshotChangeTag: String? = nil,
        remoteRecords: [CloudKitMappedRecord] = [],
        sentOperationIDs: Set<String> = [],
        sentIssueIDs: Set<String> = [],
        snapshotDirty: Bool = false,
        requiresRebase: Bool = false,
        accountIsolationRequired: Bool = false,
        failedIssueReasons: [String: CloudProgressRecordFailureReason] = [:],
        issueQueueConflict: Bool = false,
        lastFailureReason: CloudProgressRecordFailureReason? = nil,
        mergeSnapshot: ProgressMergeSnapshot? = nil,
        recoveryCheckpoint: SyncRecoveryCheckpoint? = nil,
        pendingCompactionDeleteIDs: Set<String> = []
    ) {
        self.envelope = envelope
        self.engineState = engineState
        self.changeToken = changeToken
        self.snapshotChangeTag = snapshotChangeTag
        self.remoteRecords = remoteRecords
        self.sentOperationIDs = sentOperationIDs
        self.sentIssueIDs = sentIssueIDs
        self.snapshotDirty = snapshotDirty
        self.requiresRebase = requiresRebase
        self.accountIsolationRequired = accountIsolationRequired
        self.failedIssueReasons = failedIssueReasons
        self.issueQueueConflict = issueQueueConflict
        self.lastFailureReason = lastFailureReason
        self.mergeSnapshot = mergeSnapshot
        self.recoveryCheckpoint = recoveryCheckpoint
        self.pendingCompactionDeleteIDs = pendingCompactionDeleteIDs
    }

    private enum CodingKeys: String, CodingKey {
        case envelope, engineState, changeToken, snapshotChangeTag, remoteRecords, sentOperationIDs,
             sentIssueIDs, snapshotDirty, requiresRebase, failedIssueReasons,
             issueQueueConflict, accountIsolationRequired, lastFailureReason, mergeSnapshot,
             recoveryCheckpoint, pendingCompactionDeleteIDs
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        envelope = try container.decode(ProgressEnvelope.self, forKey: .envelope)
        engineState = try container.decodeIfPresent(Data.self, forKey: .engineState)
        changeToken = try container.decodeIfPresent(Data.self, forKey: .changeToken)
        snapshotChangeTag = try container.decodeIfPresent(String.self, forKey: .snapshotChangeTag)
        remoteRecords = try container.decodeIfPresent([CloudKitMappedRecord].self, forKey: .remoteRecords) ?? []
        sentOperationIDs = try container.decodeIfPresent(Set<String>.self, forKey: .sentOperationIDs) ?? []
        sentIssueIDs = try container.decodeIfPresent(Set<String>.self, forKey: .sentIssueIDs) ?? []
        snapshotDirty = try container.decodeIfPresent(Bool.self, forKey: .snapshotDirty) ?? false
        requiresRebase = try container.decodeIfPresent(Bool.self, forKey: .requiresRebase) ?? false
        accountIsolationRequired = try container.decodeIfPresent(Bool.self, forKey: .accountIsolationRequired) ?? false
        failedIssueReasons = try container.decodeIfPresent([String: CloudProgressRecordFailureReason].self, forKey: .failedIssueReasons) ?? [:]
        issueQueueConflict = try container.decodeIfPresent(Bool.self, forKey: .issueQueueConflict) ?? false
        lastFailureReason = try container.decodeIfPresent(CloudProgressRecordFailureReason.self, forKey: .lastFailureReason)
        mergeSnapshot = try container.decodeIfPresent(ProgressMergeSnapshot.self, forKey: .mergeSnapshot)
        recoveryCheckpoint = try container.decodeIfPresent(SyncRecoveryCheckpoint.self, forKey: .recoveryCheckpoint)
        pendingCompactionDeleteIDs = try container.decodeIfPresent(Set<String>.self, forKey: .pendingCompactionDeleteIDs) ?? []
    }
}

public protocol CloudProgressPersistence: Sendable {
    func load() throws -> CloudProgressCheckpoint?
    func save(_ checkpoint: CloudProgressCheckpoint) throws
}

/// File-backed atomic persistence for production. The engine serialization is
/// encoded as Data inside the same replacement as the progress envelope.
public final class CloudProgressFileStore: @unchecked Sendable, CloudProgressPersistence {
    private let url: URL

    public init(url: URL) { self.url = url }

    public func load() throws -> CloudProgressCheckpoint? {
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        let data: Data
        do {
            data = try Data(contentsOf: url)
        } catch {
            throw CloudProgressRepositoryError.persistenceUnavailable
        }
        do {
            return try JSONDecoder().decode(CloudProgressCheckpoint.self, from: data)
        } catch {
            throw CloudProgressRepositoryError.corruptState
        }
    }

    public func save(_ checkpoint: CloudProgressCheckpoint) throws {
        let data: Data
        do {
            data = try JSONEncoder().encode(checkpoint)
        } catch {
            throw CloudProgressRepositoryError.statePersistenceFailed
        }
        do {
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try data.write(to: url, options: Self.writeOptions)
        } catch {
            throw CloudProgressRepositoryError.persistenceUnavailable
        }
    }

    private static var writeOptions: Data.WritingOptions {
        #if os(iOS) || os(tvOS) || os(watchOS)
        return [.atomic, .completeFileProtectionUntilFirstUserAuthentication]
        #else
        return [.atomic]
        #endif
    }
}

/// In-memory persistence is useful for deterministic tests and previews. It
/// still implements the same single-checkpoint contract as the file store.
public final class CloudProgressMemoryStore: @unchecked Sendable, CloudProgressPersistence {
    private let lock = NSLock()
    private var checkpoint: CloudProgressCheckpoint?
    private var shouldFail = false

    public init(checkpoint: CloudProgressCheckpoint? = nil) { self.checkpoint = checkpoint }

    public func load() throws -> CloudProgressCheckpoint? { lock.withLock { checkpoint } }

    public func save(_ checkpoint: CloudProgressCheckpoint) throws {
        try lock.withLock {
            if shouldFail { throw CloudProgressRepositoryError.statePersistenceFailed }
            self.checkpoint = checkpoint
        }
    }

    #if DEBUG
    public func failWrites(_ value: Bool = true) { lock.withLock { shouldFail = value } }
    #endif
}

/// Private-zone CloudKit progress repository. It has no automatic sync loop:
/// callers explicitly invoke `fetch()` or `send()`, while CKSyncEngine delegate
/// events are fed through `handle(_:)` and remain serial with this actor.
public actor CloudProgressRepository {
    private let actorID: String
    private let persistence: any CloudProgressPersistence
    private let transport: any CloudProgressTransport
    private let retryPolicy: CloudProgressRetryPolicy
    private var checkpoint: CloudProgressCheckpoint
    private var reachable = true
    private var retryAttempt = 0
    private var statusHistoryStorage: [SyncStatusEvent] = []
    private var statusContinuations: [UUID: AsyncStream<SyncStatusEvent>.Continuation] = [:]

    public init(
        actorID: String,
        persistence: any CloudProgressPersistence,
        transport: any CloudProgressTransport,
        retryPolicy: CloudProgressRetryPolicy = .init()
    ) throws {
        guard !actorID.isEmpty else { throw CloudProgressRepositoryError.invalidOperation }
        self.actorID = actorID
        self.persistence = persistence
        self.transport = transport
        self.retryPolicy = retryPolicy
        if let stored = try persistence.load() {
            guard stored.envelope.actorID == actorID else {
                throw CloudProgressRepositoryError.corruptState
            }
            guard stored.envelope.issues.count <= CloudKitContract.maximumQueuedIssues,
                  stored.failedIssueReasons.keys.allSatisfy({ issueID in
                      stored.envelope.issues.contains { $0.issueID == issueID }
                  }) else {
                throw CloudProgressRepositoryError.corruptState
            }
            if let mergeSnapshot = stored.mergeSnapshot,
               mergeSnapshot.envelope != stored.envelope {
                throw CloudProgressRepositoryError.corruptState
            }
            if let recovery = stored.recoveryCheckpoint,
               recovery.snapshot.envelope.actorID != actorID {
                throw CloudProgressRepositoryError.corruptState
            }
            self.checkpoint = stored
        } else {
            self.checkpoint = CloudProgressCheckpoint(envelope: ProgressEnvelope(actorID: actorID))
        }
    }

    public func snapshot() -> ProgressEnvelope { checkpoint.envelope }

    public func checkpointSnapshot() -> CloudProgressCheckpoint { checkpoint }

    public func pendingRecords() throws -> [CloudKitMappedRecord] {
        try recordsToSend()
    }

    public func statusHistory() -> [SyncStatusEvent] { statusHistoryStorage }

    /// Creates a stream for all status surfaces. Events never contain IDs or
    /// payloads; the stream is safe to hand to SwiftUI, macOS, or tests.
    public func statusEvents() -> AsyncStream<SyncStatusEvent> {
        let id = UUID()
        let stream = AsyncStream<SyncStatusEvent>.makeStream(of: SyncStatusEvent.self)
        statusContinuations[id] = stream.continuation
        stream.continuation.onTermination = { [weak self] _ in
            guard let self else { return }
            Task { await self.removeStatusStream(id) }
        }
        return stream.stream
    }

    public func removeStatusStream(_ id: UUID) {
        statusContinuations[id]?.finish()
        statusContinuations.removeValue(forKey: id)
    }

    @discardableResult
    public func save(
        _ session: SessionDetail,
        operationID: String? = nil,
        now: Date = Date()
    ) throws -> ProgressOperation {
        let id = operationID ?? UUID().uuidString.lowercased()
        if let existing = checkpoint.envelope.operations.first(where: { $0.id == id }) {
            guard existing.session == session else { throw CloudProgressRepositoryError.invalidOperation }
            return existing
        }
        let operation = ProgressOperation(operationID: id, createdAt: now, status: .applied, session: session)
        var updated = checkpoint
        Self.applyLocalSession(session, operation: operation, to: &updated.envelope)
        updated.mergeSnapshot = try Self.deriveMergeSnapshot(from: updated.envelope, trustedNow: now)
        updated.sentOperationIDs.remove(id)
        updated.snapshotDirty = true
        try commit(updated)
        emit(.init(
            state: .idle,
            reason: .initialised,
            pendingOperationCount: pendingOperationCount(),
            pendingIssueCount: pendingIssueCount()
        ))
        return operation
    }

    @discardableResult
    public func enqueue(_ operation: ProgressOperation) throws -> ProgressOperation {
        guard operation.status == .pending else { throw CloudProgressRepositoryError.invalidOperation }
        if let existing = checkpoint.envelope.operations.first(where: { $0.id == operation.id }) {
            guard existing.session == operation.session else { throw CloudProgressRepositoryError.invalidOperation }
            return existing
        }
        var updated = checkpoint
        Self.applyLocalSession(operation.session, operation: operation, to: &updated.envelope)
        updated.mergeSnapshot = try Self.deriveMergeSnapshot(from: updated.envelope, trustedNow: operation.updatedAt)
        updated.sentOperationIDs.remove(operation.id)
        updated.snapshotDirty = true
        try commit(updated)
        emitPending()
        return operation
    }

    @discardableResult
    public func queueIssue(_ issue: QuestionIssue) throws -> QuestionIssue {
        if let existing = checkpoint.envelope.issues.first(where: { $0.issueID == issue.issueID }) {
            guard existing == issue else { throw CloudProgressRepositoryError.invalidOperation }
            return existing
        }
        guard checkpoint.envelope.issues.count < CloudKitContract.maximumQueuedIssues else {
            throw CloudProgressRepositoryError.issueQueueFull
        }
        var updated = checkpoint
        updated.envelope.issues.append(issue)
        updated.mergeSnapshot = try Self.deriveMergeSnapshot(from: updated.envelope, trustedNow: Date())
        updated.sentIssueIDs.remove(issue.issueID)
        updated.failedIssueReasons.removeValue(forKey: issue.issueID)
        updated.snapshotDirty = true
        try commit(updated)
        emitPending()
        return issue
    }

    /// Explicitly fetches server changes. This method is the only repository
    /// path that calls the transport's fetch operation.
    public func fetch() async throws -> CloudProgressFetchResult {
        emit(.init(
            state: .syncing,
            reason: .explicitFetch,
            pendingOperationCount: pendingOperationCount(),
            pendingIssueCount: pendingIssueCount()
        ))
        guard reachable else {
            emitFailure(state: .offline, reason: .unreachable)
            throw CloudProgressRepositoryError.offline
        }
        do {
            let result = try await transport.fetchChanges(full: checkpoint.requiresRebase)
            if result.tokenExpired {
                try await handle(.tokenExpired)
                throw CloudProgressRepositoryError.tokenExpired
            }
            if !result.records.isEmpty {
                try await handle(.fetched(result.records))
            }
            if let snapshotChangeTag = result.snapshotChangeTag,
               snapshotChangeTag != checkpoint.snapshotChangeTag {
                var updated = checkpoint
                updated.snapshotChangeTag = snapshotChangeTag
                try commit(updated)
            }
            retryAttempt = 0
            // A successful empty fetch is the authoritative baseline for a
            // newly-created/empty zone after a rebase. Do not leave that
            // durable gate stuck merely because there was no snapshot row.
            let fullSnapshotApplied = result.isFullSnapshot
                || result.records.contains { $0.kind == .snapshot }
            if checkpoint.requiresRebase,
               result.isFullSnapshot,
               result.records.isEmpty,
               !checkpoint.accountIsolationRequired {
                // A full fetch which proves that the zone has no snapshot is
                // a new CAS baseline, not an incremental no-op. Preserve the
                // local folded facts and intents, but do not try to create a
                // record at the deleted zone's old revision: a competing
                // creator must be able to win the first conditional create.
                var updated = checkpoint
                for index in updated.envelope.operations.indices {
                    updated.envelope.operations[index].serverRevision = nil
                }
                updated.envelope.documentRevision = 0
                updated.envelope.compaction.watermarkRevision = 0
                updated.sentOperationIDs.removeAll()
                // The new zone has no immutable issue records either. Keep
                // retained reports visible, but allow their same-ID payloads
                // to be replayed idempotently into the replacement zone.
                updated.sentIssueIDs.removeAll()
                // A terminal failure is a deliberate local refusal, not an
                // acknowledgement from the deleted zone; retain its redacted
                // reason until an explicit retry or replacement report.
                updated.snapshotChangeTag = nil
                updated.changeToken = nil
                updated.remoteRecords.removeAll()
                updated.pendingCompactionDeleteIDs.removeAll()
                updated.recoveryCheckpoint = nil
                updated.requiresRebase = false
                updated.issueQueueConflict = false
                updated.mergeSnapshot = try Self.deriveMergeSnapshot(
                    from: updated.envelope,
                    trustedNow: Date()
                )
                updated.snapshotDirty = true
                try commit(updated)
            }
            if checkpoint.requiresRebase && !checkpoint.accountIsolationRequired && fullSnapshotApplied {
                var updated = checkpoint
                updated.requiresRebase = false
                try commit(updated)
            }
            if checkpoint.accountIsolationRequired {
                emitFailure(state: .accountIsolationRequired, reason: .accountChanged)
            } else if checkpoint.snapshotDirty {
                emit(.init(
                    state: .idle,
                    reason: .statePersisted,
                    pendingOperationCount: pendingOperationCount(),
                    pendingIssueCount: pendingIssueCount()
                ))
            } else {
                emit(.init(
                    state: .synced,
                    reason: .completed,
                    pendingOperationCount: pendingOperationCount(),
                    pendingIssueCount: pendingIssueCount()
                ))
            }
            return result
        } catch let error as CloudProgressRepositoryError {
            throw error
        } catch let error as CloudKitMappingError {
            if error == .encodedSizeRefused {
                emitFailure(state: .failed, reason: .encodedSizeRefused)
                throw CloudProgressRepositoryError.encodedSizeRefused
            }
            emitFailure(state: .failed, reason: .malformedRecord)
            throw CloudProgressRepositoryError.malformedRecord
        } catch let error as CloudProgressTransportError {
            try await handleTransportFailure(error)
            throw map(error)
        } catch {
            emitFailure(state: .failed, reason: .recordFailure)
            throw CloudProgressRepositoryError.transportUnavailable
        }
    }

    /// Explicitly sends mapped records. The delegate is not involved in
    /// deciding when this method runs.
    public func send() async throws -> CloudProgressSendResult {
        emit(.init(
            state: .syncing,
            reason: .explicitSend,
            pendingOperationCount: pendingOperationCount(),
            pendingIssueCount: pendingIssueCount()
        ))
        guard reachable else {
            emitFailure(state: .offline, reason: .unreachable)
            throw CloudProgressRepositoryError.offline
        }
        do {
            guard !checkpoint.requiresRebase else {
                if checkpoint.accountIsolationRequired {
                    emitFailure(state: .accountIsolationRequired, reason: .accountChanged)
                    throw CloudProgressRepositoryError.accountIsolationRequired
                }
                emitFailure(state: .rebasing, reason: .rebaseRequired)
                throw CloudProgressRepositoryError.rebaseRequired
            }
            guard !checkpoint.issueQueueConflict else {
                emitFailure(state: .partialFailure, reason: .recordFailure)
                throw CloudProgressRepositoryError.issueQueueFull
            }
            var aggregateResult = CloudProgressSendResult()
            while true {
                try resumeRecoveryBeforeSend()
                guard !hasUnresolvedTerminalFailures else {
                    emitFailure(state: .partialFailure, reason: .recordFailure)
                    throw CloudProgressRepositoryError.partialFailure
                }
                let records = try recordsToSend()
                guard !records.isEmpty || !checkpoint.pendingCompactionDeleteIDs.isEmpty else {
                    emit(.init(state: .synced, reason: .completed))
                    return aggregateResult
                }
                let expectedRevision = max(
                    checkpoint.envelope.documentRevision,
                    checkpoint.mergeSnapshot?.envelope.documentRevision ?? 0
                )
                var result = try await sendRecordBatches(
                    records,
                    expectedRevision: expectedRevision,
                    snapshotChangeTag: checkpoint.snapshotChangeTag
                )
                try applyAuthoritativeAssignments(result.assignedRevisions)
                if let snapshotChangeTag = result.snapshotChangeTag,
                   snapshotChangeTag != checkpoint.snapshotChangeTag {
                    var updated = checkpoint
                    updated.snapshotChangeTag = snapshotChangeTag
                    try commit(updated)
                }
            // The CAS returns only newly saved operation records. Existing
            // server-known records in an idempotent retry are acknowledged by
            // their validated authoritative assignment instead of being
            // mistaken for unsent local work.
            var acknowledgedNames = result.savedRecordNames
            for operationID in result.assignedRevisions.keys {
                if checkpoint.envelope.operations.contains(where: { $0.id == operationID }) {
                    acknowledgedNames.append("\(CloudKitRecordKind.operation.rawValue)/\(operationID)")
                }
            }
            try await handle(.sent(Array(Set(acknowledgedNames))))
            if !checkpoint.pendingCompactionDeleteIDs.isEmpty {
                let names = checkpoint.pendingCompactionDeleteIDs.compactMap {
                    try? CloudKitContract.recordName(for: .operation, identifier: $0)
                }.sorted()
                let deleteResult = try await transport.deleteChanges(names)
                result = combine(result, deleteResult)
            }
            try await handle(.deleted(result.deletedRecordNames))
            for serverRecord in result.serverRecords {
                try await handle(.serverRecordChanged(serverRecord))
            }
            let retryableFailures = result.failedRecords.filter(\.retryable)
            for failure in result.failedRecords where !failure.retryable {
                try markTerminalFailure(failure)
            }
            if !retryableFailures.isEmpty {
                retryAttempt += 1
                let delay = retryPolicy.delayMilliseconds(forAttempt: retryAttempt)
                if retryAttempt > retryPolicy.maximumAttempts {
                    for failure in retryableFailures { try markTerminalFailure(failure) }
                    emitFailure(state: .failed, reason: .recordFailure)
                } else {
                    emit(.init(
                        state: .retryScheduled,
                        reason: .retryBackoff,
                        pendingOperationCount: pendingOperationCount(),
                        pendingIssueCount: pendingIssueCount(),
                        retryAttempt: retryAttempt,
                        retryAfterMilliseconds: delay
                    ))
                }
            }
            if !result.failedRecords.isEmpty {
                if retryableFailures.isEmpty {
                    emit(.init(
                        state: .partialFailure,
                        reason: .recordFailure,
                        pendingOperationCount: pendingOperationCount(),
                        pendingIssueCount: pendingIssueCount(),
                        retryAttempt: retryAttempt
                    ))
                }
                throw CloudProgressRepositoryError.partialFailure
            }
            retryAttempt = 0
            aggregateResult = combine(aggregateResult, result)
            if hasUnsentPublishableOperations {
                continue
            }
            if checkpoint.snapshotDirty {
                // An issue acknowledgement removes that independent queue
                // entry, but the snapshot in the same batch still describes
                // the pre-ack queue. Do not report a completed sync until the
                // derived snapshot is acknowledged by a subsequent send.
                emit(.init(
                    state: .idle,
                    reason: .statePersisted,
                    pendingOperationCount: pendingOperationCount(),
                    pendingIssueCount: pendingIssueCount()
                ))
            } else {
                emit(.init(
                    state: .synced,
                    reason: .completed,
                    pendingOperationCount: pendingOperationCount(),
                    pendingIssueCount: pendingIssueCount()
                ))
            }
            return aggregateResult
            }
        } catch let error as CloudProgressRepositoryError {
            throw error
        } catch let error as CloudKitMappingError {
            let reason: SyncStatusReason = error == .encodedSizeRefused
                ? .encodedSizeRefused
                : .malformedRecord
            emitFailure(state: .failed, reason: reason)
            throw error == .encodedSizeRefused
                ? CloudProgressRepositoryError.encodedSizeRefused
                : CloudProgressRepositoryError.malformedRecord
        } catch let error as CloudProgressTransportError {
            try await handleTransportFailure(error)
            throw map(error)
        } catch {
            emitFailure(state: .failed, reason: .recordFailure)
            throw CloudProgressRepositoryError.transportUnavailable
        }
    }

    /// Handles one serial CKSyncEngine event. It never invokes fetch/send;
    /// account and token events only clear pending engine changes and mark the
    /// full-snapshot recovery path visibly required.
    public func handle(_ event: CloudProgressEngineEvent) async throws {
        switch event {
        case let .stateUpdate(data):
            var updated = checkpoint
            updated.engineState = data
            try commit(updated)
            emit(.init(
                state: .idle,
                reason: .statePersisted,
                pendingOperationCount: pendingOperationCount(),
                pendingIssueCount: pendingIssueCount()
            ))
        case .accountChanged:
            await resetEngineForRecovery(reason: .accountChanged)
        case .containerChanged:
            await resetEngineForRecovery(reason: .containerChanged)
        case .tokenExpired:
            await resetEngineForRecovery(reason: .tokenExpired)
        case let .fetched(records):
            do {
                for record in records { _ = try CloudKitMapping.decode(record) }
                var updated = checkpoint
                updated = try mergeFetchedRecords(records, into: updated, trustedNow: Date())
                let names = Set(records.map(\.recordName))
                updated.remoteRecords.removeAll { names.contains($0.recordName) }
                updated.remoteRecords.append(contentsOf: records)
                for record in records where record.kind == .operation {
                    let operation = try CloudKitMapping.operation(from: record)
                    if operation.serverRevision != nil {
                        updated.sentOperationIDs.insert(operation.id)
                    }
                }
                try commit(updated)
            } catch let error as CloudKitMappingError {
                let reason: SyncStatusReason
                switch error {
                case .unsupportedSchemaVersion, .incompatibleVersion:
                    reason = .incompatibleVersion
                default:
                    reason = .malformedRecord
                }
                emitFailure(state: .failed, reason: reason)
                throw CloudProgressRepositoryError.malformedRecord
            } catch let error as ProgressMergeError {
                if case .revisionConflict = error {
                    await resetEngineForRecovery(reason: .rebaseRequired)
                    throw CloudProgressRepositoryError.rebaseRequired
                }
                emitFailure(state: .failed, reason: .malformedRecord)
                throw CloudProgressRepositoryError.malformedRecord
            } catch let error as CloudProgressRepositoryError {
                if error == .issueQueueFull {
                    var blocked = checkpoint
                    blocked.requiresRebase = false
                    blocked.issueQueueConflict = true
                    let names = Set(records.map(\.recordName))
                    blocked.remoteRecords.removeAll { names.contains($0.recordName) }
                    blocked.remoteRecords.append(contentsOf: records)
                    try commit(blocked)
                    emitFailure(state: .partialFailure, reason: .recordFailure)
                }
                throw error
            } catch {
                emitFailure(state: .failed, reason: .statePersistenceFailed)
                throw CloudProgressRepositoryError.statePersistenceFailed
            }
        case let .sent(recordNames):
            var updated = checkpoint
            markSent(recordNames, in: &updated)
            try commit(updated)
        case let .deleted(recordNames):
            let deleted = Set(recordNames)
            var updated = checkpoint
            updated.remoteRecords.removeAll { deleted.contains($0.recordName) }
            // A remote deletion is not an acknowledgement of local work. If
            // a retained local record was deleted remotely, derive it again
            // from the envelope on the next explicit send.
            for name in deleted {
                if let operationID = operationID(from: name),
                   updated.envelope.operations.contains(where: { $0.id == operationID }) {
                    updated.sentOperationIDs.remove(operationID)
                    updated.snapshotDirty = true
                }
                if let issueID = issueID(from: name),
                   updated.envelope.issues.contains(where: { $0.issueID == issueID }) {
                    updated.sentIssueIDs.remove(issueID)
                    updated.snapshotDirty = true
                }
                if name == CloudKitContract.snapshotRecordName {
                    updated.snapshotDirty = true
                }
                if let operationID = operationID(from: name) {
                    updated.pendingCompactionDeleteIDs.remove(operationID)
                }
            }
            if updated.pendingCompactionDeleteIDs.isEmpty,
               updated.recoveryCheckpoint?.stage == .completed {
                updated.recoveryCheckpoint = nil
            }
            try commit(updated)
        case let .serverRecordChanged(record):
            do { _ = try CloudKitMapping.decode(record) }
            catch {
                emitFailure(state: .failed, reason: .malformedRecord)
                throw CloudProgressRepositoryError.malformedRecord
            }
            var updated = checkpoint
            updated.remoteRecords.removeAll { $0.recordName == record.recordName }
            updated.remoteRecords.append(record)
            try commit(updated)
            emit(.init(
                state: .conflict,
                reason: .serverRecordChanged,
                pendingOperationCount: pendingOperationCount(),
                pendingIssueCount: pendingIssueCount()
            ))
        case let .recordFailure(failure):
            if failure.retryable {
                retryAttempt += 1
                let delay = retryPolicy.delayMilliseconds(forAttempt: retryAttempt)
                if retryAttempt > retryPolicy.maximumAttempts {
                    try markTerminalFailure(failure)
                    emitFailure(state: .failed, reason: .recordFailure)
                } else {
                    emit(.init(
                        state: .retryScheduled,
                        reason: .retryBackoff,
                        pendingOperationCount: pendingOperationCount(),
                        pendingIssueCount: pendingIssueCount(),
                        retryAttempt: retryAttempt,
                        retryAfterMilliseconds: delay
                    ))
                }
            } else {
                try markTerminalFailure(failure)
                emit(.init(
                    state: .partialFailure,
                    reason: .recordFailure,
                    pendingOperationCount: pendingOperationCount(),
                    pendingIssueCount: pendingIssueCount(),
                    retryAttempt: retryAttempt
                ))
            }
        case let .reachability(value):
            reachable = value
            if value { retryAttempt = 0 }
            emit(.init(
                state: value ? .idle : .offline,
                reason: value ? .reachable : .unreachable,
                pendingOperationCount: pendingOperationCount(),
                pendingIssueCount: pendingIssueCount()
            ))
        }
    }

    private func combine(_ left: CloudProgressSendResult, _ right: CloudProgressSendResult) -> CloudProgressSendResult {
        CloudProgressSendResult(
            savedRecordNames: left.savedRecordNames + right.savedRecordNames,
            deletedRecordNames: left.deletedRecordNames + right.deletedRecordNames,
            failedRecords: left.failedRecords + right.failedRecords,
            serverRecords: left.serverRecords + right.serverRecords,
            snapshotChangeTag: right.snapshotChangeTag ?? left.snapshotChangeTag,
            assignedRevisions: left.assignedRevisions.merging(right.assignedRevisions) { _, right in right }
        )
    }

    private func sendRecordBatches(
        _ records: [CloudKitMappedRecord],
        expectedRevision: Int,
        snapshotChangeTag: String?
    ) async throws -> CloudProgressSendResult {
        guard !records.isEmpty else { return CloudProgressSendResult() }
        var result = CloudProgressSendResult()
        let progress = records.filter { $0.kind == .snapshot || $0.kind == .operation }
        let snapshot = progress.first { $0.kind == .snapshot }
        let operations = progress.filter { $0.kind == .operation }
        if snapshot != nil {
            // Keep the authoritative snapshot and the first operation batch
            // in one atomic compare-and-swap. Additional operation batches
            // remain below the service limit and are sent afterward.
            let atomicOperations = Array(operations.prefix(ProgressMergeLimits.maximumRecordsPerBatch - 1))
            let atomicSnapshot = try atomicSnapshotRecord(
                operationIDs: Set(atomicOperations.map(\.recordName)),
                expectedRevision: expectedRevision
            )
            let atomicRecords = [atomicSnapshot] + atomicOperations
            let atomicResult = try await transport.sendProgressAtomically(
                atomicRecords,
                expectedRevision: expectedRevision,
                snapshotChangeTag: snapshotChangeTag
            )
            let atomicOperationIDs = Set(atomicOperations.compactMap { record -> String? in
                guard case let .string(operationID) = record.fields["operation_id"] else { return nil }
                return operationID
            })
            let newlySavedOperationNames = atomicOperations
                .filter { $0.fields["server_revision"] == nil }
                .map(\.recordName)
            guard !atomicResult.failedRecords.isEmpty || (
                Set(atomicResult.assignedRevisions.keys) == atomicOperationIDs
                    && atomicResult.snapshotChangeTag != nil
                    && Set(atomicResult.savedRecordNames).isSuperset(
                        of: [atomicSnapshot.recordName] + newlySavedOperationNames
                    )
            ) else {
                throw CloudProgressRepositoryError.rebaseRequired
            }
            result = combine(result, atomicResult)
            let atomicNames = Set(atomicRecords.map(\.recordName))
            // Remaining operation records wait for the next reservation/CAS
            // pass; they must never be published against the earlier
            // snapshot proposal outside the atomic commit.
            let remaining = records.filter {
                !atomicNames.contains($0.recordName) && $0.kind != .operation
            }
            for start in stride(from: 0, to: remaining.count, by: ProgressMergeLimits.maximumRecordsPerBatch) {
                let batch = Array(remaining[start..<min(start + ProgressMergeLimits.maximumRecordsPerBatch, remaining.count)])
                if !batch.isEmpty {
                    result = combine(result, try await transport.sendIssuesAtomically(batch))
                }
            }
        } else {
            for start in stride(from: 0, to: records.count, by: ProgressMergeLimits.maximumRecordsPerBatch) {
                let batch = Array(records[start..<min(start + ProgressMergeLimits.maximumRecordsPerBatch, records.count)])
                let issues = batch.filter { $0.kind == .issue }
                if !issues.isEmpty {
                    result = combine(result, try await transport.sendIssuesAtomically(issues))
                }
            }
        }
        return result
    }

    /// A snapshot CAS may publish only the operation records in that same
    /// atomic request. Locally queued operations remain in the checkpoint but
    /// are omitted from this wire snapshot until a later CAS assigns them a
    /// revision and publishes their records together.
    private func atomicSnapshotRecord(
        operationIDs: Set<String>,
        expectedRevision: Int
    ) throws -> CloudKitMappedRecord {
        var envelope = checkpoint.envelope
        envelope.operations = envelope.operations.filter { operation in
            let recordName = "\(CloudKitRecordKind.operation.rawValue)/\(operation.id)"
            return checkpoint.sentOperationIDs.contains(operation.id)
                || operation.serverRevision != nil
                || operationIDs.contains(recordName)
        }
        envelope.documentRevision = expectedRevision
        if expectedRevision > 0,
           let key = envelope.operations
               .filter({ $0.serverRevision == expectedRevision })
               .map(\.id)
               .max(by: { Array($0.utf8).lexicographicallyPrecedes(Array($1.utf8)) }) {
            envelope.operationID = key
        }
        return try CloudKitMapping.snapshotRecord(envelope)
    }

    private func applyAuthoritativeAssignments(_ assignments: [String: Int]) throws {
        guard !assignments.isEmpty else { return }
        var updated = checkpoint
        let expectedIDs = Set(updated.envelope.operations.compactMap { operation in
            assignments[operation.id] == nil ? nil : operation.id
        })
        guard expectedIDs == Set(assignments.keys),
              assignments.values.allSatisfy({ $0 > 0 }),
              Set(assignments.values).count == assignments.values.count else {
            throw CloudProgressRepositoryError.rebaseRequired
        }
        for index in updated.envelope.operations.indices {
            if let revision = assignments[updated.envelope.operations[index].id] {
                guard updated.envelope.operations[index].serverRevision == nil
                    || updated.envelope.operations[index].serverRevision == revision else {
                    throw CloudProgressRepositoryError.rebaseRequired
                }
                updated.envelope.operations[index].serverRevision = revision
            }
        }
        if let highest = assignments.max(by: { left, right in
            if left.value != right.value { return left.value < right.value }
            return Array(left.key.utf8).lexicographicallyPrecedes(Array(right.key.utf8))
        }) {
            if highest.value > updated.envelope.documentRevision {
                updated.envelope.documentRevision = highest.value
                updated.envelope.operationID = highest.key
            } else if highest.value == updated.envelope.documentRevision {
                updated.envelope.operationID = highest.key
            }
        }
        updated.mergeSnapshot = try Self.deriveMergeSnapshot(
            from: updated.envelope,
            trustedNow: Date()
        )
        try commit(updated)
    }

    private func resumeRecoveryBeforeSend() throws {
        let trustedNow = Date()
        var updated = checkpoint
        if updated.recoveryCheckpoint == nil {
            let snapshot = try currentMergeSnapshot(trustedNow: trustedNow)
            let pendingIDs = Set(updated.envelope.operations.filter { operation in
                !updated.sentOperationIDs.contains(operation.id)
            }.map(\.id))
            let initial = try SyncRecoveryMachine.begin(
                snapshot: snapshot,
                pendingOperations: [],
                pendingEngineOperationIDs: pendingIDs,
                now: trustedNow
            )
            updated.mergeSnapshot = snapshot
            guard !(initial.compactionPlan?.deletedOperationIDs.isEmpty ?? true) else {
                if updated.mergeSnapshot != checkpoint.mergeSnapshot { try commit(updated) }
                return
            }
            updated.recoveryCheckpoint = initial
            try commit(updated)
        }

        while let recovery = updated.recoveryCheckpoint, recovery.stage != .completed {
            let next = try SyncRecoveryMachine.step(recovery)
            updated.recoveryCheckpoint = next
            updated.mergeSnapshot = next.snapshot
            updated.envelope = next.snapshot.envelope
            if next.snapshot != recovery.snapshot {
                updated.snapshotDirty = true
            }
            try commit(updated)
        }
        if let recovery = updated.recoveryCheckpoint, recovery.stage == .completed {
            updated.pendingCompactionDeleteIDs.formUnion(recovery.deletedOperationIDs)
            updated.mergeSnapshot = recovery.snapshot
            updated.envelope = recovery.snapshot.envelope
            updated.snapshotDirty = true
            try commit(updated)
        }
    }

    private static func deriveMergeSnapshot(
        from envelope: ProgressEnvelope,
        trustedNow: Date
    ) throws -> ProgressMergeSnapshot {
        let operations = envelope.operations.compactMap { operation -> ProgressMergeOperation? in
            guard operation.status != .failed,
                  let session = operation.session,
                  let revision = operation.serverRevision,
                  revision > 0 else { return nil }
            return ProgressMergeOperation(
                operationID: operation.id,
                baseRevision: max(0, revision - 1),
                serverRevision: revision,
                createdAt: operation.createdAt,
                updatedAt: operation.updatedAt,
                serverRecordedAt: trustedNow,
                session: session
            )
        }
        return try ProgressMergeSnapshot(envelope: envelope, operations: operations)
    }

    /// Applies local facts without fabricating a new global revision. The
    /// shared cursor advances only after the authoritative transport reserves
    /// a server revision for the operation.
    private static func applyLocalSession(
        _ session: SessionDetail?,
        operation: ProgressOperation,
        to envelope: inout ProgressEnvelope
    ) {
        let authoritativeRevision = envelope.documentRevision
        let authoritativeOperationID = envelope.operationID
        let priorOperations = envelope.operations
        if let session {
            envelope.applying(session, operation: operation)
        } else {
            envelope.operations.append(operation)
        }
        let retainedIDs = Set(envelope.operations.map(\.id))
        envelope.operations.append(contentsOf: priorOperations.filter { !retainedIDs.contains($0.id) })
        envelope.documentRevision = authoritativeRevision
        envelope.operationID = authoritativeOperationID
    }

    private func mergeFetchedRecords(
        _ records: [CloudKitMappedRecord],
        into original: CloudProgressCheckpoint,
        trustedNow: Date
    ) throws -> CloudProgressCheckpoint {
        var updated = original
        let snapshotRecord = records.first(where: { $0.kind == .snapshot })
        let baseEnvelope = try snapshotRecord.map(CloudKitMapping.snapshot(from:)) ?? original.envelope
        var base = try Self.deriveMergeSnapshot(from: baseEnvelope, trustedNow: trustedNow)
        if snapshotRecord == nil, let existing = original.mergeSnapshot {
            base = existing
        }

        let remoteOperationRecords = records.filter { $0.kind == .operation }
        var incoming: [ProgressMergeOperation] = []
        for record in remoteOperationRecords {
            let operation = try CloudKitMapping.operation(from: record)
            guard let session = operation.session else { continue }
            guard let serverRevision = operation.serverRevision, serverRevision > 0 else {
                throw CloudProgressRepositoryError.malformedRecord
            }
            // A verified server record is not a local send candidate. Keep
            // the acknowledgement separate from the folded operation state.
            updated.sentOperationIDs.insert(operation.id)
            incoming.append(ProgressMergeOperation(
                operationID: operation.id,
                baseRevision: max(0, serverRevision - 1),
                serverRevision: serverRevision,
                createdAt: operation.createdAt,
                updatedAt: operation.updatedAt,
                serverRecordedAt: trustedNow,
                session: session
            ))
        }

        // Preserve local intents which were not present in a fetched full
        // snapshot. They are replayed through the same deterministic reducer,
        // never by ad-hoc envelope mutation.
        let knownIDs = Set(base.operations.map(\.operationID))
        let incomingIDs = Set(incoming.map(\.operationID))
        for operation in original.envelope.operations {
            guard operation.status != .failed,
                  let session = operation.session,
                  let serverRevision = operation.serverRevision,
                  serverRevision > 0,
                  !knownIDs.contains(operation.id),
                  !incomingIDs.contains(operation.id) else { continue }
            incoming.append(ProgressMergeOperation(
                operationID: operation.id,
                baseRevision: max(0, serverRevision - 1),
                serverRevision: serverRevision,
                createdAt: operation.createdAt,
                updatedAt: operation.updatedAt,
                serverRecordedAt: trustedNow,
                session: session
            ))
        }
        let merged = try ProgressMergeEngine.merge(incoming, into: base, now: trustedNow)
        let mergedEnvelope = merged.snapshot.envelope
        // The remote snapshot's actor identifies its writer. The local
        // checkpoint remains owned by this device while retaining the
        // cross-device folded facts and operation log.
        var rebasedEnvelope = ProgressEnvelope(
            schemaVersion: mergedEnvelope.schemaVersion,
            documentRevision: mergedEnvelope.documentRevision,
            actorID: actorID,
            operationID: mergedEnvelope.operationID,
            createdAt: mergedEnvelope.createdAt,
            sessionDetails: mergedEnvelope.sessionDetails,
            aggregate: mergedEnvelope.aggregate,
            mastery: mergedEnvelope.mastery,
            srs: mergedEnvelope.srs,
            compaction: mergedEnvelope.compaction,
            operations: mergedEnvelope.operations,
            issues: mergedEnvelope.issues
        )

        // Issue reports are independent queue entries. A remote snapshot may
        // be from another device and therefore cannot be allowed to erase a
        // local unsent or failed report during rebase.
        let remoteIssueIDs = Set(rebasedEnvelope.issues.map(\.issueID))
        for issue in original.envelope.issues
        where !original.sentIssueIDs.contains(issue.issueID)
            && !remoteIssueIDs.contains(issue.issueID) {
            guard rebasedEnvelope.issues.count < CloudKitContract.maximumQueuedIssues else {
                throw CloudProgressRepositoryError.issueQueueFull
            }
            rebasedEnvelope.issues.append(issue)
        }
        let rebasedIDs = Set(rebasedEnvelope.operations.map(\.id))
        for operation in original.envelope.operations where
            operation.status != .failed
            && operation.session != nil
            && operation.serverRevision == nil
            && !rebasedIDs.contains(operation.id) {
            Self.applyLocalSession(operation.session, operation: operation, to: &rebasedEnvelope)
        }
        updated.envelope = rebasedEnvelope
        updated.issueQueueConflict = false
        updated.failedIssueReasons = original.failedIssueReasons.filter { issueID, _ in
            rebasedEnvelope.issues.contains { $0.issueID == issueID }
        }
        updated.mergeSnapshot = try ProgressMergeSnapshot(
            envelope: rebasedEnvelope,
            operations: merged.snapshot.operations,
            tombstones: merged.snapshot.tombstones
        )
        return updated
    }

    private func currentMergeSnapshot(trustedNow: Date = Date()) throws -> ProgressMergeSnapshot {
        if let mergeSnapshot = checkpoint.mergeSnapshot { return mergeSnapshot }
        return try Self.deriveMergeSnapshot(from: checkpoint.envelope, trustedNow: trustedNow)
    }

    private func recordsToSend() throws -> [CloudKitMappedRecord] {
        var records: [CloudKitMappedRecord] = []
        for operation in checkpoint.envelope.operations
        where !checkpoint.sentOperationIDs.contains(operation.id) && operation.status != .failed {
            // The atomic transport assigns a revision while publishing the
            // snapshot. A local operation therefore remains publishable with
            // a nil revision; no client-side position is invented here.
            guard operation.session != nil else { continue }
            records.append(try CloudKitMapping.operationRecord(operation))
        }
        for issue in checkpoint.envelope.issues
        where !checkpoint.sentIssueIDs.contains(issue.issueID)
            && checkpoint.failedIssueReasons[issue.issueID] == nil {
            records.append(try CloudKitMapping.issueRecord(issue))
        }
        if !hasUnresolvedTerminalFailures && (checkpoint.snapshotDirty || !records.isEmpty) {
            records.append(try CloudKitMapping.snapshotRecord(checkpoint.envelope))
        }
        return records.sorted { $0.recordName < $1.recordName }
    }

    private var hasUnresolvedTerminalFailures: Bool {
        checkpoint.envelope.operations.contains { $0.status == .failed }
            || !checkpoint.failedIssueReasons.isEmpty
    }

    private var hasUnsentPublishableOperations: Bool {
        checkpoint.envelope.operations.contains {
            $0.status != .failed
                && $0.session != nil
                && $0.serverRevision == nil
                && !checkpoint.sentOperationIDs.contains($0.id)
        }
    }

    private func markSent(_ names: [String], in checkpoint: inout CloudProgressCheckpoint) {
        let nameSet = Set(names)
        var acknowledgedIssueIDs = Set<String>()
        for operation in checkpoint.envelope.operations {
            if nameSet.contains("\(CloudKitRecordKind.operation.rawValue)/\(operation.id)") {
                checkpoint.sentOperationIDs.insert(operation.id)
            }
        }
        for issue in checkpoint.envelope.issues {
            if nameSet.contains("\(CloudKitRecordKind.issue.rawValue)/\(issue.issueID)") {
                checkpoint.sentIssueIDs.insert(issue.issueID)
                acknowledgedIssueIDs.insert(issue.issueID)
            }
        }
        if !acknowledgedIssueIDs.isEmpty {
            // An issue is an independent immutable report, not progress
            // history. Remove it only after its own record was acknowledged;
            // a crash before this checkpoint leaves it retryable.
            checkpoint.envelope.issues.removeAll { acknowledgedIssueIDs.contains($0.issueID) }
            checkpoint.sentIssueIDs.subtract(acknowledgedIssueIDs)
            for issueID in acknowledgedIssueIDs {
                checkpoint.failedIssueReasons.removeValue(forKey: issueID)
            }
            // The snapshot in this same batch predates the queue removal and
            // must be published again before the checkpoint is clean.
            checkpoint.snapshotDirty = true
        } else if nameSet.contains(CloudKitContract.snapshotRecordName) {
            checkpoint.snapshotDirty = false
        }
    }

    private func markTerminalFailure(_ failure: CloudProgressRecordFailure) throws {
        var updated = checkpoint
        applyTerminalFailure(failure, to: &updated)
        try commit(updated)
    }

    private func applyTerminalFailure(
        _ failure: CloudProgressRecordFailure,
        to checkpoint: inout CloudProgressCheckpoint
    ) {
        switch failure.target {
        case .operation:
            guard let operationID = operationID(from: failure.recordName),
                  let index = checkpoint.envelope.operations.firstIndex(where: { $0.id == operationID }) else {
                checkpoint.lastFailureReason = failure.reason
                return
            }
            checkpoint.envelope.operations[index].status = .failed
            // Never retain transport text or a remote identifier in the
            // durable operation error.
            checkpoint.envelope.operations[index].error = .failed("cloud_sync_failed")
            checkpoint.sentOperationIDs.remove(operationID)
            checkpoint.snapshotDirty = true
        case .issue:
            guard let issueID = issueID(from: failure.recordName),
                  checkpoint.envelope.issues.contains(where: { $0.issueID == issueID }) else {
                checkpoint.lastFailureReason = failure.reason
                return
            }
            checkpoint.failedIssueReasons[issueID] = failure.reason
            checkpoint.sentIssueIDs.remove(issueID)
            checkpoint.snapshotDirty = true
        case .snapshot, .unknown:
            checkpoint.lastFailureReason = failure.reason
            checkpoint.snapshotDirty = true
        }
    }

    private func operationID(from recordName: String) -> String? {
        let prefix = "\(CloudKitRecordKind.operation.rawValue)/"
        guard recordName.hasPrefix(prefix) else { return nil }
        let id = String(recordName.dropFirst(prefix.count))
        guard !id.isEmpty, (try? CloudKitContract.recordName(for: .operation, identifier: id)) == recordName else {
            return nil
        }
        return id
    }

    private func issueID(from recordName: String) -> String? {
        let prefix = "\(CloudKitRecordKind.issue.rawValue)/"
        guard recordName.hasPrefix(prefix) else { return nil }
        let id = String(recordName.dropFirst(prefix.count))
        guard !id.isEmpty, (try? CloudKitContract.recordName(for: .issue, identifier: id)) == recordName else {
            return nil
        }
        return id
    }

    private func resetEngineForRecovery(reason: SyncStatusReason) async {
        var updated = checkpoint
        updated.engineState = nil
        updated.changeToken = nil
        updated.snapshotChangeTag = nil
        updated.remoteRecords = []
        updated.requiresRebase = true
        if reason == .rebaseRequired {
            for index in updated.envelope.operations.indices
            where !updated.sentOperationIDs.contains(updated.envelope.operations[index].id) {
                updated.envelope.operations[index].serverRevision = nil
            }
            updated.mergeSnapshot = try? Self.deriveMergeSnapshot(
                from: updated.envelope,
                trustedNow: Date()
            )
        }
        if reason == .accountChanged { updated.accountIsolationRequired = true }
        // Recovery must publish a fresh snapshot even when all operation and
        // issue records were previously acknowledged. The next send derives
        // every still-unsent record from this local envelope, rather than
        // trusting CKSyncEngine's discarded pending-change list.
        updated.snapshotDirty = true
        do {
            try commit(updated)
        } catch {
            return
        }
        await transport.resetPendingChanges()
        emit(.init(
            state: reason == .accountChanged ? .accountIsolationRequired : .rebasing,
            reason: reason,
            pendingOperationCount: pendingOperationCount(),
            pendingIssueCount: pendingIssueCount()
        ))
    }

    private func handleTransportFailure(_ error: CloudProgressTransportError) async throws {
        switch error {
        case .offline, .network:
            retryAttempt += 1
            let delay = retryPolicy.delayMilliseconds(forAttempt: retryAttempt)
            emit(.init(
                state: .retryScheduled,
                reason: .retryBackoff,
                pendingOperationCount: pendingOperationCount(),
                pendingIssueCount: pendingIssueCount(),
                retryAttempt: retryAttempt,
                retryAfterMilliseconds: delay
            ))
        case .accountUnavailable:
            emitFailure(state: .accountUnavailable, reason: .recordFailure)
        case .containerChanged:
            await resetEngineForRecovery(reason: .containerChanged)
        case .tokenExpired:
            await resetEngineForRecovery(reason: .tokenExpired)
        case .serverRecordChanged:
            await resetEngineForRecovery(reason: .rebaseRequired)
            emitFailure(state: .conflict, reason: .serverRecordChanged)
        case .unavailable:
            emitFailure(state: .failed, reason: .recordFailure)
        }
    }

    private func commit(_ updated: CloudProgressCheckpoint) throws {
        var synchronized = updated
        do {
            if let mergeSnapshot = synchronized.mergeSnapshot {
                synchronized.mergeSnapshot = try ProgressMergeSnapshot(
                    envelope: synchronized.envelope,
                    operations: mergeSnapshot.operations,
                    tombstones: mergeSnapshot.tombstones
                )
            } else {
                synchronized.mergeSnapshot = try Self.deriveMergeSnapshot(
                    from: synchronized.envelope,
                    trustedNow: Date()
                )
            }
        } catch {
            emitFailure(state: .failed, reason: .statePersistenceFailed)
            throw CloudProgressRepositoryError.statePersistenceFailed
        }
        do {
            try persistence.save(synchronized)
        } catch {
            emitFailure(state: .failed, reason: .statePersistenceFailed)
            throw CloudProgressRepositoryError.statePersistenceFailed
        }
        checkpoint = synchronized
    }

    private func map(_ error: CloudProgressTransportError) -> CloudProgressRepositoryError {
        switch error {
        case .offline: return .offline
        case .accountUnavailable: return .accountUnavailable
        case .tokenExpired: return .tokenExpired
        case .containerChanged: return .corruptState
        case .network, .unavailable: return .transportUnavailable
        case .serverRecordChanged: return .rebaseRequired
        }
    }

    private func pendingOperationCount() -> Int {
        checkpoint.envelope.operations.reduce(into: 0) { count, operation in
            if !checkpoint.sentOperationIDs.contains(operation.id) { count += 1 }
        }
    }

    private func pendingIssueCount() -> Int {
        checkpoint.envelope.issues.reduce(into: 0) { count, issue in
            if !checkpoint.sentIssueIDs.contains(issue.issueID) { count += 1 }
        }
    }

    private func emitPending() {
        emit(.init(
            state: .idle,
            reason: .initialised,
            pendingOperationCount: pendingOperationCount(),
            pendingIssueCount: pendingIssueCount()
        ))
    }

    private func emitFailure(state: SyncStatusState, reason: SyncStatusReason) {
        emit(.init(
            state: state,
            reason: reason,
            pendingOperationCount: pendingOperationCount(),
            pendingIssueCount: pendingIssueCount(),
            retryAttempt: retryAttempt
        ))
    }

    private func emit(_ event: SyncStatusEvent) {
        statusHistoryStorage.append(event)
        for continuation in statusContinuations.values { continuation.yield(event) }
    }

}

#if canImport(CloudKit)
/// CKSyncEngine-backed transport for the private QuizzlerProgress zone. The
/// delegate only records state and change events; it never calls fetch/send.
@available(iOS 17.0, macOS 14.0, *)
public final class CKSyncEngineCloudProgressTransport: @unchecked Sendable, CloudProgressTransport {
    private let engine: CKSyncEngine
    private let database: CKDatabase
    private let zoneID: CKRecordZone.ID
    private let delegate: Delegate

    public init(
        containerIdentifier: String,
        stateSerialization: CKSyncEngine.State.Serialization? = nil,
        event: @escaping @Sendable (CloudProgressEngineEvent) -> Void = { _ in }
    ) {
        let zoneID = CKRecordZone.ID(zoneName: CloudKitContract.zoneName)
        self.zoneID = zoneID
        self.delegate = Delegate(event: event)
        let container = CKContainer(identifier: containerIdentifier)
        self.database = container.privateCloudDatabase
        var configuration = CKSyncEngine.Configuration(
            database: container.privateCloudDatabase,
            stateSerialization: stateSerialization,
            delegate: delegate
        )
        configuration.automaticallySync = false
        configuration.subscriptionID = CloudKitContract.subscriptionID
        self.engine = CKSyncEngine(configuration)
    }

    public func fetchChanges() async throws -> CloudProgressFetchResult {
        delegate.beginFetch()
        do {
            try await engine.fetchChanges(.init(scope: .zoneIDs([zoneID])))
            return delegate.takeFetchResult()
        } catch {
            let result = delegate.takeFetchResult()
            let mapped = Self.map(error)
            if mapped == .tokenExpired {
                return CloudProgressFetchResult(records: result.records, tokenExpired: true)
            }
            throw mapped
        }
    }

    public func fetchChanges(full: Bool) async throws -> CloudProgressFetchResult {
        guard full else { return try await fetchChanges() }
        do {
            _ = try await database.modifyRecordZones(
                saving: [CKRecordZone(zoneID: zoneID)],
                deleting: []
            )
            let snapshotID = CKRecord.ID(recordName: CloudKitContract.snapshotRecordName, zoneID: zoneID)
            do {
                let record = try await database.record(for: snapshotID)
                let mapped = try CloudKitMappedRecord(ckRecord: record)
                guard mapped.kind == .snapshot else { throw CloudKitMappingError.recordTypeMismatch }
                return CloudProgressFetchResult(
                    records: [mapped],
                    snapshotChangeTag: record.recordChangeTag,
                    isFullSnapshot: true
                )
            } catch let error as NSError
                where error.domain == CKErrorDomain && error.code == CKError.Code.unknownItem.rawValue {
                return CloudProgressFetchResult(isFullSnapshot: true)
            }
        } catch let error as CloudProgressTransportError {
            throw error
        } catch {
            throw Self.map(error)
        }
    }

    public func sendProgressAtomically(
        _ records: [CloudKitMappedRecord],
        expectedRevision: Int,
        snapshotChangeTag: String?
    ) async throws -> CloudProgressSendResult {
        guard expectedRevision >= 0,
              let snapshot = records.first(where: { $0.kind == .snapshot }),
              records.filter({ $0.kind == .snapshot }).count == 1,
              records.allSatisfy({ $0.kind == .snapshot || $0.kind == .operation }) else {
            throw CloudProgressTransportError.unavailable
        }
        do {
            // Direct CKDatabase CAS is not zone-creating. Establish the
            // custom zone explicitly before the first snapshot read/write.
            _ = try await database.modifyRecordZones(
                saving: [CKRecordZone(zoneID: zoneID)],
                deleting: []
            )
        } catch {
            throw Self.map(error)
        }
        let snapshotID = CKRecord.ID(recordName: snapshot.recordName, zoneID: zoneID)
        let authoritative: CKRecord?
        do {
            authoritative = try await database.record(for: snapshotID)
        } catch let error as NSError
            where error.domain == CKErrorDomain && error.code == CKError.Code.unknownItem.rawValue {
            authoritative = nil
        } catch {
            throw Self.map(error)
        }

        let proposedEnvelope = try CloudKitMapping.snapshot(from: snapshot)
        let authoritativeMapped = try authoritative.map { try CloudKitMappedRecord(ckRecord: $0) }
        let authoritativeEnvelope = try authoritativeMapped.map { try CloudKitMapping.snapshot(from: $0) }
        if let authoritativeEnvelope {
            guard authoritativeEnvelope.documentRevision == expectedRevision else {
                throw CloudProgressTransportError.serverRecordChanged
            }
        } else {
            guard expectedRevision == 0 else { throw CloudProgressTransportError.serverRecordChanged }
        }
        let operationRecords = try records
            .filter { $0.kind == .operation }
            .map { record -> (ProgressOperation, CloudKitMappedRecord) in
                (try CloudKitMapping.operation(from: record), record)
            }
        let operationIDs = operationRecords.map { $0.0.id }
        guard Set(operationIDs).count == operationIDs.count else {
            throw CloudProgressTransportError.unavailable
        }
        let existingOperations = Dictionary(uniqueKeysWithValues: (authoritativeEnvelope?.operations ?? []).map { ($0.id, $0) })
        var revisions: [String: Int] = [:]
        var newOperationIDs: [String] = []
        for (operation, _) in operationRecords {
            if let existing = existingOperations[operation.id] {
                var comparableOperation = operation
                comparableOperation.serverRevision = nil
                var comparableExisting = existing
                comparableExisting.serverRevision = nil
                guard let revision = existing.serverRevision,
                      operation.serverRevision == nil || operation.serverRevision == revision,
                      comparableOperation == comparableExisting else {
                    throw CloudProgressTransportError.serverRecordChanged
                }
                revisions[operation.id] = revision
            } else {
                guard operation.serverRevision == nil else {
                    throw CloudProgressTransportError.serverRecordChanged
                }
                newOperationIDs.append(operation.id)
            }
        }
        let newRevisions = newOperationIDs
            .sorted { Self.utf8($0).lexicographicallyPrecedes(Self.utf8($1)) }
            .enumerated()
            .map { ($0.element, expectedRevision + $0.offset + 1) }
        revisions.merge(newRevisions, uniquingKeysWith: { _, right in right })
        var assignedEnvelope = proposedEnvelope
        for index in assignedEnvelope.operations.indices {
            guard let revision = revisions[assignedEnvelope.operations[index].id] else { continue }
            assignedEnvelope.operations[index].serverRevision = revision
        }
        guard revisions.keys.allSatisfy({ operationID in
            assignedEnvelope.operations.contains { $0.id == operationID }
        }) else {
            throw CloudProgressTransportError.unavailable
        }
        let baselineRevision = max(expectedRevision, assignedEnvelope.documentRevision)
        assignedEnvelope.documentRevision = baselineRevision
        if let highest = revisions.max(by: { left, right in
            if left.value != right.value { return left.value < right.value }
            return Self.utf8(left.key).lexicographicallyPrecedes(Self.utf8(right.key))
        }) {
            if highest.value > baselineRevision {
                assignedEnvelope.documentRevision = highest.value
                assignedEnvelope.operationID = highest.key
            } else if highest.value == baselineRevision {
                assignedEnvelope.operationID = highest.key
            }
        }
        let assignedSnapshot = try CloudKitMapping.snapshotRecord(assignedEnvelope)

        let savingSnapshot: CKRecord
        if let authoritative {
            guard snapshotChangeTag == nil || snapshotChangeTag == authoritative.recordChangeTag,
                  (authoritative["document_revision"] as? NSNumber)?.intValue == expectedRevision else {
                throw CloudProgressTransportError.serverRecordChanged
            }
            savingSnapshot = authoritative.copy() as! CKRecord
            try Self.apply(assignedSnapshot, to: savingSnapshot)
        } else {
            savingSnapshot = try assignedSnapshot.makeCKRecord(in: zoneID)
        }
        let savingOperationRecords = try operationRecords.filter { operation, _ in
            newOperationIDs.contains(operation.id)
        }.map { operation, _ in
            try CloudKitMapping.operationRecord(
                operation,
                serverRevision: revisions[operation.id]
            ).makeCKRecord(in: zoneID)
        }
        // A retry after a successful CAS may contain only already-published
        // operations. In that case the exact snapshot/tag is proof of the
        // prior commit and no second save is necessary.
        if savingOperationRecords.isEmpty, authoritative != nil,
           assignedEnvelope == authoritativeEnvelope {
            return CloudProgressSendResult(
                savedRecordNames: records.map(\.recordName),
                snapshotChangeTag: authoritative?.recordChangeTag,
                assignedRevisions: revisions
            )
        }
        let saving = [savingSnapshot] + savingOperationRecords
        do {
            let result = try await database.modifyRecords(
                saving: saving,
                deleting: [],
                savePolicy: .ifServerRecordUnchanged,
                atomically: true
            )
            for record in saving {
                guard let saveResult = result.saveResults[record.recordID] else {
                    throw CloudProgressTransportError.unavailable
                }
                if case let .failure(error) = saveResult {
                    throw Self.map(error)
                }
            }
            let savedSnapshotTag: String?
            if case let .success(savedSnapshot) = result.saveResults[savingSnapshot.recordID] {
                savedSnapshotTag = savedSnapshot.recordChangeTag
            } else {
                savedSnapshotTag = nil
            }
            return CloudProgressSendResult(
                savedRecordNames: saving.map(\.recordID.recordName),
                snapshotChangeTag: savedSnapshotTag,
                assignedRevisions: revisions
            )
        } catch let error as CloudProgressTransportError {
            throw error
        } catch {
            throw Self.map(error)
        }
    }

    public func sendIssuesAtomically(_ records: [CloudKitMappedRecord]) async throws -> CloudProgressSendResult {
        guard records.allSatisfy({ $0.kind == .issue }) else {
            throw CloudProgressTransportError.unavailable
        }
        do {
            _ = try await database.modifyRecordZones(
                saving: [CKRecordZone(zoneID: zoneID)],
                deleting: []
            )
            var newRecords: [CKRecord] = []
            var savedNames: [String] = []
            for mapped in records {
                let id = CKRecord.ID(recordName: mapped.recordName, zoneID: zoneID)
                do {
                    let existing = try await database.record(for: id)
                    let existingMapped = try CloudKitMappedRecord(ckRecord: existing)
                    guard existingMapped == mapped else {
                        throw CloudProgressTransportError.serverRecordChanged
                    }
                    savedNames.append(mapped.recordName)
                } catch let error as NSError
                    where error.domain == CKErrorDomain && error.code == CKError.Code.unknownItem.rawValue {
                    newRecords.append(try mapped.makeCKRecord(in: zoneID))
                }
            }
            if !newRecords.isEmpty {
                let result = try await database.modifyRecords(
                    saving: newRecords,
                    deleting: [],
                    savePolicy: .ifServerRecordUnchanged,
                    atomically: true
                )
                for record in newRecords {
                    guard let saveResult = result.saveResults[record.recordID] else {
                        throw CloudProgressTransportError.unavailable
                    }
                    if case let .failure(error) = saveResult {
                        throw Self.map(error)
                    }
                    savedNames.append(record.recordID.recordName)
                }
            }
            return CloudProgressSendResult(savedRecordNames: savedNames)
        } catch let error as CloudProgressTransportError {
            throw error
        } catch {
            throw Self.map(error)
        }
    }

    public func sendChanges(_ records: [CloudKitMappedRecord]) async throws -> CloudProgressSendResult {
        do {
            let records = try records.map { try $0.makeCKRecord(in: zoneID) }
            delegate.set(records)
            delegate.beginSend()
            if !records.isEmpty {
                engine.state.add(pendingRecordZoneChanges: records.map { .saveRecord($0.recordID) })
            }
            try await engine.sendChanges(.init(scope: .zoneIDs([zoneID])))
            return delegate.takeSendResult()
        } catch {
            throw Self.map(error)
        }
    }

    public func deleteChanges(_ recordNames: [String]) async throws -> CloudProgressSendResult {
        do {
            let recordIDs = try recordNames.map { name -> CKRecord.ID in
                guard name.hasPrefix("\(CloudKitRecordKind.operation.rawValue)/") else {
                    throw CloudProgressRepositoryError.invalidOperation
                }
                let identifier = String(name.dropFirst(CloudKitRecordKind.operation.rawValue.count + 1))
                return CKRecord.ID(recordName: try CloudKitContract.recordName(for: .operation, identifier: identifier), zoneID: zoneID)
            }
            delegate.beginSend()
            if !recordIDs.isEmpty {
                engine.state.add(pendingRecordZoneChanges: recordIDs.map { .deleteRecord($0) })
            }
            try await engine.sendChanges(.init(scope: .zoneIDs([zoneID])))
            return delegate.takeSendResult()
        } catch let error as CloudProgressRepositoryError {
            throw error
        } catch {
            throw Self.map(error)
        }
    }

    public func resetPendingChanges() async {
        engine.state.remove(pendingRecordZoneChanges: engine.state.pendingRecordZoneChanges)
        engine.state.remove(pendingDatabaseChanges: engine.state.pendingDatabaseChanges)
    }

    private static func map(_ error: Error) -> CloudProgressTransportError {
        if cloudSyncProbeIsServerRecordChanged(error) {
            return .serverRecordChanged
        }
        let nsError = error as NSError
        guard nsError.domain == CKErrorDomain, let code = CKError.Code(rawValue: nsError.code) else {
            return .unavailable
        }
        switch code {
        case .notAuthenticated, .accountTemporarilyUnavailable: return .accountUnavailable
        case .networkFailure, .serviceUnavailable, .requestRateLimited: return .network
        case .changeTokenExpired: return .tokenExpired
        case .serverRecordChanged: return .serverRecordChanged
        case .zoneNotFound: return .containerChanged
        default: return .unavailable
        }
    }

    private static func utf8(_ value: String) -> [UInt8] { Array(value.utf8) }

    private static func apply(_ mapped: CloudKitMappedRecord, to record: CKRecord) throws {
        for (key, value) in mapped.fields {
            switch value {
            case let .string(value): record[key] = value as NSString
            case let .integer(value): record[key] = NSNumber(value: value)
            case let .boolean(value): record[key] = NSNumber(value: value)
            case let .date(value): record[key] = value as NSDate
            case let .data(value): record[key] = value as NSData
            }
        }
    }

    private final class Delegate: NSObject, CKSyncEngineDelegate, @unchecked Sendable {
        private let event: @Sendable (CloudProgressEngineEvent) -> Void
        private let lock = NSLock()
        private var records: [CKRecord.ID: CKRecord] = [:]
        private var savedRecordNames: [String] = []
        private var deletedRecordNames: [String] = []
        private var failures: [CloudProgressRecordFailure] = []
        private var serverRecords: [CloudKitMappedRecord] = []
        private var fetchedRecords: [String: CloudKitMappedRecord] = [:]
        private var fetchedSnapshotChangeTag: String?
        private var fetchTokenExpired = false

        init(event: @escaping @Sendable (CloudProgressEngineEvent) -> Void) {
            self.event = event
        }

        func handleEvent(_ event: CKSyncEngine.Event, syncEngine: CKSyncEngine) async {
            switch event {
            case let .stateUpdate(update):
                if let data = try? JSONEncoder().encode(update.stateSerialization) {
                    self.event(.stateUpdate(data))
                }
            case .accountChange:
                self.event(.accountChanged)
            case let .fetchedDatabaseChanges(changes):
                if !changes.deletions.isEmpty {
                    self.event(.containerChanged)
                }
            case let .fetchedRecordZoneChanges(changes):
                let mapped = changes.modifications.compactMap { try? CloudKitMappedRecord(ckRecord: $0.record) }
                lock.withLock {
                    for record in mapped { fetchedRecords[record.recordName] = record }
                    if let snapshot = changes.modifications.first(where: { $0.record.recordType == CloudKitRecordKind.snapshot.recordType }) {
                        fetchedSnapshotChangeTag = snapshot.record.recordChangeTag
                    }
                }
            case let .didFetchRecordZoneChanges(changes):
                if changes.error?.code == .changeTokenExpired {
                    lock.withLock { fetchTokenExpired = true }
                    // Token expiry is part of the fetch result, not a
                    // lifecycle callback. Returning it exactly once prevents
                    // a repository-wired event closure from applying recovery
                    // twice for one fetch attempt.
                }
            case let .sentRecordZoneChanges(changes):
                for record in changes.savedRecords {
                    lock.withLock {
                        records.removeValue(forKey: record.recordID)
                        savedRecordNames.append(record.recordID.recordName)
                    }
                }
                for failure in changes.failedRecordSaves {
                    let isConflict = cloudSyncProbeIsServerRecordChanged(failure.error)
                    if isConflict,
                       let serverRecord = failure.error.userInfo[CKRecordChangedErrorServerRecordKey] as? CKRecord,
                       let mapped = try? CloudKitMappedRecord(ckRecord: serverRecord) {
                        lock.withLock { serverRecords.append(mapped) }
                    } else {
                        let mapped = CloudProgressRecordFailure(
                            recordName: failure.record.recordID.recordName,
                            reason: isConflict ? .serverRecordChanged : .unknown,
                            retryable: !isConflict
                        )
                        lock.withLock { failures.append(mapped) }
                    }
                }
                let deleted = changes.deletedRecordIDs.map(\.recordName)
                if !deleted.isEmpty {
                    lock.withLock { deletedRecordNames.append(contentsOf: deleted) }
                }
            default:
                break
            }
        }

        func nextRecordZoneChangeBatch(
            _ context: CKSyncEngine.SendChangesContext,
            syncEngine: CKSyncEngine
        ) async -> CKSyncEngine.RecordZoneChangeBatch? {
            let changes = syncEngine.state.pendingRecordZoneChanges.filter { context.options.scope.contains($0) }
            guard !changes.isEmpty else { return nil }
            let batch = Array(changes.prefix(CloudKitContract.maximumRecordsPerBatch))
            return await CKSyncEngine.RecordZoneChangeBatch(pendingChanges: batch) { [weak self] recordID in
                self?.record(for: recordID)
            }
        }

        func nextFetchChangesOptions(
            _ context: CKSyncEngine.FetchChangesContext,
            syncEngine: CKSyncEngine
        ) async -> CKSyncEngine.FetchChangesOptions { context.options }

        func set(_ records: [CKRecord]) {
            lock.withLock {
                for record in records { self.records[record.recordID] = record }
            }
        }

        func record(for id: CKRecord.ID) -> CKRecord? { lock.withLock { records[id] } }

        func beginFetch() {
            lock.withLock {
                fetchedRecords.removeAll()
                fetchedSnapshotChangeTag = nil
                fetchTokenExpired = false
            }
        }

        func takeFetchResult() -> CloudProgressFetchResult {
            lock.withLock {
                defer {
                    fetchedRecords.removeAll()
                    fetchedSnapshotChangeTag = nil
                    fetchTokenExpired = false
                }
                return CloudProgressFetchResult(
                    records: fetchedRecords.values.sorted { $0.recordName < $1.recordName },
                    tokenExpired: fetchTokenExpired,
                    snapshotChangeTag: fetchedSnapshotChangeTag
                )
            }
        }

        func beginSend() {
            lock.withLock {
                savedRecordNames.removeAll()
                deletedRecordNames.removeAll()
                failures.removeAll()
                serverRecords.removeAll()
            }
        }

        func takeSendResult() -> CloudProgressSendResult {
            lock.withLock {
                defer {
                    savedRecordNames.removeAll()
                    deletedRecordNames.removeAll()
                    failures.removeAll()
                    serverRecords.removeAll()
                }
                return CloudProgressSendResult(
                    savedRecordNames: savedRecordNames,
                    deletedRecordNames: deletedRecordNames,
                    failedRecords: failures,
                    serverRecords: serverRecords
                )
            }
        }
    }
}
#else
public struct CKSyncEngineCloudProgressTransport: Sendable, CloudProgressTransport {
    public init(containerIdentifier: String) {}
    public func fetchChanges() async throws -> CloudProgressFetchResult { throw CloudProgressTransportError.unavailable }
    public func sendChanges(_ records: [CloudKitMappedRecord]) async throws -> CloudProgressSendResult { throw CloudProgressTransportError.unavailable }
    public func resetPendingChanges() async {}
}
#endif
