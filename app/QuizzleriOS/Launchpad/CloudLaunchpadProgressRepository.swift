import CloudKit
import CryptoKit
import Foundation
import QuizzlerKit

/// The launchpad's normal progress store: an on-device CloudKit checkpoint
/// with an explicit synchronisation boundary. CloudKit is never a prerequisite
/// for saving an answer; every mutation reaches the checkpoint before a fetch
/// or send is attempted.
actor CloudLaunchpadProgressRepository: LaunchpadProgressRepository {
    nonisolated let syncMode: LaunchpadSyncMode = .cloudKit

    private let cloud: CloudProgressRepository
    private let legacyStore: LocalProgressStore
    private let migrationMarkerURL: URL
    private let legacyProgressURL: URL?
    private let importAuthorizationMarkerURL: URL?
    private var migrationChecked = false

    init(
        cloud: CloudProgressRepository,
        legacyStore: LocalProgressStore,
        migrationMarkerURL: URL,
        legacyProgressURL: URL? = nil,
        importAuthorizationMarkerURL: URL? = nil
    ) {
        self.cloud = cloud
        self.legacyStore = legacyStore
        self.migrationMarkerURL = migrationMarkerURL
        self.legacyProgressURL = legacyProgressURL
        self.importAuthorizationMarkerURL = importAuthorizationMarkerURL
    }

    func snapshot() async throws -> ProgressEnvelope {
        await cloud.snapshot()
    }

    func setMaximumLeitnerLevel(_ maximum: Int) async throws -> ProgressEnvelope {
        _ = try await cloud.setMaximumLeitnerLevel(maximum)
        return await cloud.snapshot()
    }

    func reviewHistory(for identity: QuestionIdentity) async throws -> QuestionReviewHistory {
        try await cloud.reviewHistory(for: identity)
    }

    func progressSnapshots() async -> AsyncStream<ProgressEnvelope> {
        await cloud.progressSnapshots()
    }

    func syncStatusEvents() async -> AsyncStream<SyncStatusEvent> {
        await cloud.statusEvents()
    }

    func save(_ session: SessionDetail) async throws -> ProgressOperation {
        try await cloud.save(session)
    }

    func queueIssue(_ issue: QuestionIssue) async throws -> QuestionIssue {
        try await cloud.queueIssue(issue)
    }

    /// Fetch before sending. That ordering lets a second device merge its
    /// retained legacy sessions with the first device's CloudKit baseline
    /// rather than overwriting it with a blind local snapshot.
    func synchronize() async throws {
        try await migrateLegacyProgressIfNecessary()
        _ = try await cloud.fetch(full: true)
        let authorizedImportOperationIDs = try await importAuthorizationOperationIDsIfPresent()
        if let authorizedImportOperationIDs {
            try await cloud.releaseAccountIsolationForAuthorizedImport(
                expectedOperationIDs: authorizedImportOperationIDs
            )
        }
        _ = try await cloud.send()
        if authorizedImportOperationIDs != nil,
           let markerURL = importAuthorizationMarkerURL {
            let checkpoint = await cloud.checkpointSnapshot()
            guard Self.isConfirmedSent(checkpoint) else {
                throw CloudLaunchpadProgressError.importNotConfirmed
            }
            do {
                try FileManager.default.removeItem(at: markerURL)
            } catch {
                // Retaining the marker is safe; the next successful
                // synchronization can retry this cleanup without reopening
                // account isolation.
                throw CloudLaunchpadProgressError.importAuthorizationMarkerUnavailable
            }
        }
    }

    private func importAuthorizationOperationIDsIfPresent() async throws -> Set<String>? {
        guard let markerURL = importAuthorizationMarkerURL,
              FileManager.default.fileExists(atPath: markerURL.path) else {
            return nil
        }
        guard let legacyProgressURL,
              let legacyPayload = try? Data(contentsOf: legacyProgressURL) else {
            throw CloudLaunchpadProgressError.importAuthorizationInvalid
        }
        let legacy: ProgressEnvelope
        do {
            guard let loaded = try await legacyStore.read() else {
                throw CloudLaunchpadProgressError.importAuthorizationInvalid
            }
            legacy = loaded
        } catch let error as CloudLaunchpadProgressError {
            throw error
        } catch {
            throw CloudLaunchpadProgressError.importAuthorizationInvalid
        }
        let authorization: CloudLaunchpadImportAuthorization
        do {
            authorization = try JSONDecoder().decode(
                CloudLaunchpadImportAuthorization.self,
                from: Data(contentsOf: markerURL)
            )
        } catch {
            throw CloudLaunchpadProgressError.importAuthorizationInvalid
        }
        guard authorization.version == CloudLaunchpadImportAuthorization.currentVersion,
              !authorization.nonce.isEmpty,
              UUID(uuidString: authorization.nonce) != nil,
              authorization.legacyActorID == legacy.actorID,
              authorization.legacyPayloadDigest.count == 64,
              authorization.legacyPayloadDigest.allSatisfy(\.isHexDigit) else {
            throw CloudLaunchpadProgressError.importAuthorizationInvalid
        }
        let digest = SHA256.hash(data: legacyPayload)
            .map { String(format: "%02x", $0) }
            .joined()
        guard digest == authorization.legacyPayloadDigest.lowercased() else {
            throw CloudLaunchpadProgressError.importAuthorizationInvalid
        }

        let sessions = Self.sessionsToMigrate(from: legacy)
        let answerCount = sessions.reduce(0) { $0 + $1.answers.count }
        guard answerCount == legacy.aggregate.answered else {
            throw CloudLaunchpadProgressError.legacyHistoryIsNotFullyRetained
        }
        return Set(sessions.map { "legacy-session-\($0.sessionID)" })
    }

    private static func isConfirmedSent(_ checkpoint: CloudProgressCheckpoint) -> Bool {
        !checkpoint.snapshotDirty
            && !checkpoint.requiresRebase
            && !checkpoint.accountIsolationRequired
            && checkpoint.pendingCompactionDeleteIDs.isEmpty
            && checkpoint.envelope.operations.allSatisfy {
                checkpoint.sentOperationIDs.contains($0.id)
            }
            && checkpoint.envelope.issues.allSatisfy {
                checkpoint.sentIssueIDs.contains($0.issueID)
            }
    }

    private func migrateLegacyProgressIfNecessary() async throws {
        guard !migrationChecked else { return }
        if FileManager.default.fileExists(atPath: migrationMarkerURL.path) {
            migrationChecked = true
            return
        }

        guard let legacy = try await legacyStore.read() else {
            try markMigrationComplete()
            return
        }

        // A legacy envelope may contain more aggregate history than its bounded
        // retained session log. Refuse to fabricate answers just to make a
        // counter match; the untouched local file remains the recovery source.
        let sessions = Self.sessionsToMigrate(from: legacy)
        let answerCount = sessions.reduce(0) { $0 + $1.answers.count }
        guard answerCount == legacy.aggregate.answered else {
            throw CloudLaunchpadProgressError.legacyHistoryIsNotFullyRetained
        }

        for session in sessions {
            // Session IDs are already durable IDs. The stable prefix prevents a
            // repeat after an interrupted marker write from counting twice.
            _ = try await cloud.save(
                session,
                operationID: "legacy-session-\(session.sessionID)",
                now: session.completedAt
            )
        }
        for issue in legacy.issues {
            _ = try await cloud.queueIssue(issue)
        }

        try markMigrationComplete()
    }

    private func markMigrationComplete() throws {
        do {
            try FileManager.default.createDirectory(
                at: migrationMarkerURL.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            try Data("v1".utf8).write(to: migrationMarkerURL, options: .atomic)
            migrationChecked = true
        } catch {
            // Replaying the stable session IDs on the next launch is safe; a
            // missing marker must never look like a completed migration.
            throw CloudLaunchpadProgressError.migrationMarkerUnavailable
        }
    }

    private static func sessionsToMigrate(from envelope: ProgressEnvelope) -> [SessionDetail] {
        var sessions: [String: SessionDetail] = [:]
        for operation in envelope.operations {
            if let session = operation.session {
                sessions[session.sessionID] = session
            }
        }
        for session in envelope.sessionDetails {
            sessions[session.sessionID] = session
        }
        return sessions.values.sorted { left, right in
            if left.completedAt != right.completedAt { return left.completedAt < right.completedAt }
            return left.sessionID < right.sessionID
        }
    }
}

enum CloudLaunchpadProgressError: Error, Equatable, Sendable {
    /// The old local store compacted history that cannot be reconstructed from
    /// retained sessions. Keep it untouched and fail visibly instead of
    /// creating invented answers in shared progress.
    case legacyHistoryIsNotFullyRetained
    case migrationMarkerUnavailable
    case importAuthorizationInvalid
    case importNotConfirmed
    case importAuthorizationMarkerUnavailable
}

struct CloudLaunchpadImportAuthorization: Codable, Equatable, Sendable {
    static let currentVersion = 1

    let version: Int
    let nonce: String
    let legacyActorID: String
    let legacyPayloadDigest: String
}

enum QuizzlerCloudProgressFactory {
    private static let actorIDKey = "quizzler.cloud-progress.actor-id.v1"

    static func make() -> CloudLaunchpadProgressRepository {
        do {
            let directory = try applicationSupportDirectory()
            let actorID = deviceActorID()
            let checkpointStore = CloudProgressFileStore(
                url: directory.appendingPathComponent("cloud-progress-v1.json", isDirectory: false)
            )
            // CKSyncEngine owns opaque state that must be restored before it
            // does any work. The same checkpoint is then handed to the
            // repository, keeping that state and the user-visible progress
            // in one atomic local file.
            let checkpoint = try checkpointStore.load()
            let serializedEngineState = checkpoint?.engineState.flatMap { data in
                try? JSONDecoder().decode(CKSyncEngine.State.Serialization.self, from: data)
            }
            let eventForwarder = CloudProgressEventForwarder()
            let transport = CKSyncEngineCloudProgressTransport(
                // The entitlement selects Development for Debug and Production
                // for a signed Release build. Both use this registered
                // container identifier; this code never promotes a schema.
                containerIdentifier: "iCloud.com.zerodelta.quizzler.dev",
                stateSerialization: serializedEngineState,
                event: { eventForwarder.forward($0) }
            )
            let cloud = try CloudProgressRepository(
                actorID: actorID,
                persistence: checkpointStore,
                transport: transport
            )
            eventForwarder.bind(cloud)
            return CloudLaunchpadProgressRepository(
                cloud: cloud,
                legacyStore: LocalProgressStore(
                    fileURL: directory.appendingPathComponent("progress-v1.json", isDirectory: false)
                ),
                migrationMarkerURL: directory.appendingPathComponent("cloud-progress-migration-v1", isDirectory: false),
                legacyProgressURL: directory.appendingPathComponent("progress-v1.json", isDirectory: false),
                importAuthorizationMarkerURL: directory.appendingPathComponent(
                    "cloud-progress-import-authorized-v1.json",
                    isDirectory: false
                )
            )
        } catch {
            preconditionFailure("Quizzler CloudKit checkpoint could not be initialised")
        }
    }

    private static func applicationSupportDirectory() throws -> URL {
        try FileManager.default.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        ).appendingPathComponent("Quizzler", isDirectory: true)
    }

    private static func deviceActorID(defaults: UserDefaults = .standard) -> String {
        if let existing = defaults.string(forKey: actorIDKey), !existing.isEmpty {
            return existing
        }
        let actorID = "device-\(UUID().uuidString.lowercased())"
        defaults.set(actorID, forKey: actorIDKey)
        return actorID
    }
}

/// Bridges the transport's synchronous delegate callback back to the
/// repository actor. Keeping this object alive through the transport closure
/// lets every CKSyncEngine state update reach the atomic checkpoint.
private final class CloudProgressEventForwarder: @unchecked Sendable {
    private let lock = NSLock()
    private var repository: CloudProgressRepository?
    /// Delegate callbacks are synchronous but repository handling is async.
    /// Chain each task behind its predecessor so CKSyncEngine event order is
    /// preserved across state updates, account changes, and fetched batches.
    private var eventTail: Task<Void, Never>?

    func bind(_ repository: CloudProgressRepository) {
        lock.withLock { self.repository = repository }
    }

    func forward(_ event: CloudProgressEngineEvent) {
        lock.withLock {
            let repository = self.repository
            let previous = eventTail
            eventTail = Task {
                await previous?.value
                guard let repository else { return }
                // The repository turns lifecycle events into its own recovery
                // state. A later explicit synchronise call presents that state
                // to the launchpad, so a delegate callback never crashes it.
                try? await repository.handle(event)
            }
        }
    }
}
