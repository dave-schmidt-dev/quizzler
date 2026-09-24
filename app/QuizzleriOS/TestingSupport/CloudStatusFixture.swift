import Foundation
import QuizzlerKit

#if DEBUG

/// A DEBUG-only, local-backed stand-in for `CloudLaunchpadProgressRepository`.
///
/// This never constructs a `CKSyncEngine` and never touches
/// `QuizzlerProgressRepository.production()`. It reports `syncMode ==
/// .cloudKit` (so `LaunchpadProgressModel` runs its cloud-path logic) while
/// persisting to a local `ProgressRepository`/`LocalProgressStore`, exactly
/// like `QuizzlerProgressRepository.localForUITest()`. Its first
/// `synchronize()` establishes the authoritative baseline and completes the
/// startup v1 cap migration; later calls follow the selected script, either
/// succeeding (`.synced`) or throwing a plain, non-`accountIsolationRequired`
/// error (`.syncPending`). The file name and Release source exclusion ensure
/// this fixture never ships in an archived app or release pack.
actor CloudStatusFixtureProgressRepository: LaunchpadProgressRepository {
    enum SynchronizeError: Error, Sendable, Equatable {
        /// Any non-`accountIsolationRequired` failure is enough to drive
        /// `LaunchpadProgressModel` into `.syncPending`; this name documents
        /// why the fixture throws rather than mimicking a specific transport
        /// failure.
        case scriptedSyncFailure
    }

    nonisolated let syncMode: LaunchpadSyncMode = .cloudKit

    private let local: ProgressRepository
    private let script: UITestFixture.CloudStatusScript
    private var initialSyncCompleted = false

    init(actorID: String, store: LocalProgressStore, script: UITestFixture.CloudStatusScript) {
        self.local = ProgressRepository(actorID: actorID, store: store)
        self.script = script
    }

    func snapshot() async throws -> ProgressEnvelope {
        try await local.snapshot()
    }

    func setMaximumLeitnerLevel(_ maximum: Int) async throws -> ProgressEnvelope {
        _ = try await local.setMaximumLeitnerLevel(maximum)
        return try await local.snapshot()
    }

    func reviewHistory(for identity: QuestionIdentity) async throws -> QuestionReviewHistory {
        .incomplete([])
    }

    func progressSnapshots() async -> AsyncStream<ProgressEnvelope> {
        AsyncStream { continuation in continuation.finish() }
    }

    func save(_ session: SessionDetail) async throws -> ProgressOperation {
        try await local.save(session)
    }

    func queueIssue(_ issue: QuestionIssue) async throws -> QuestionIssue {
        try await local.queueIssue(issue)
    }

    func synchronize() async throws {
        // Model the authoritative startup baseline/migration before the
        // scripted subsequent send failure exercised by pending-sync tests.
        if !initialSyncCompleted {
            let current = try await local.snapshot()
            if current.schemaVersion == 1 {
                _ = try await local.setMaximumLeitnerLevel(LeitnerSchedule.defaultMaximumLevel)
            }
            initialSyncCompleted = true
            return
        }
        switch script {
        case .synced:
            return
        case .syncPending:
            throw SynchronizeError.scriptedSyncFailure
        }
    }
}

#endif
