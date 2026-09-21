import Foundation
import QuizzlerKit

#if DEBUG

/// A DEBUG-only, local-backed stand-in for `CloudLaunchpadProgressRepository`.
///
/// This never constructs a `CKSyncEngine` and never touches
/// `QuizzlerProgressRepository.production()`. It reports `syncMode ==
/// .cloudKit` (so `LaunchpadProgressModel` runs its cloud-path logic) while
/// persisting to a local `ProgressRepository`/`LocalProgressStore`, exactly
/// like `QuizzlerProgressRepository.localForUITest()`. `synchronize()` is
/// scripted at launch to either succeed (`.synced`) or throw a plain,
/// non-`accountIsolationRequired` error (`.syncPending`) — the file name and
/// the Release source exclusion are intentional: this fixture must never be
/// present in an archived app or a release pack.
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

    init(actorID: String, store: LocalProgressStore, script: UITestFixture.CloudStatusScript) {
        self.local = ProgressRepository(actorID: actorID, store: store)
        self.script = script
    }

    func snapshot() async throws -> ProgressEnvelope {
        try await local.snapshot()
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
        switch script {
        case .synced:
            return
        case .syncPending:
            throw SynchronizeError.scriptedSyncFailure
        }
    }
}

#endif
