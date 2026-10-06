@testable import QuizzleriOS
import QuizzlerKit

/// An in-memory `LaunchpadProgressRepository` whose calls a test can count,
/// fail, and pause. It backs the launchpad progress model's local and cloud
/// paths without the network or disk.
actor ControlledProgressRepository: LaunchpadProgressRepository {
    private var aggregate = AggregateSnapshot()
    private var schemaVersion: Int
    private var maximumLeitnerLevel = LeitnerSchedule.defaultMaximumLevel
    private var maximumLevelSetCalls = 0
    private var maximumLevelSetLevels: [Int] = []
    private var snapshotCalls = 0
    private var saveCalls = 0
    private var synchronizeCalls = 0
    private var failingSnapshotCalls: Set<Int>
    private var failingMaximumLevelSetCalls: Set<Int>
    private let maximumLevelChangeGate: MaximumLevelChangeGate?
    private var snapshotContinuation: AsyncStream<ProgressEnvelope>.Continuation?
    private var snapshotStreamReady = false
    private var streamReadyWaiters: [CheckedContinuation<Void, Never>] = []
    private var statusContinuation: AsyncStream<SyncStatusEvent>.Continuation?
    private var statusStreamReady = false
    private var statusStreamReadyWaiters: [CheckedContinuation<Void, Never>] = []
    nonisolated let syncMode: LaunchpadSyncMode

    init(
        failingSnapshotCalls: Set<Int> = [],
        failingMaximumLevelSetCalls: Set<Int> = [],
        maximumLevelChangeGate: MaximumLevelChangeGate? = nil,
        syncMode: LaunchpadSyncMode = .local,
        schemaVersion: Int = ProgressEnvelope.currentSchemaVersion
    ) {
        self.failingSnapshotCalls = failingSnapshotCalls
        self.failingMaximumLevelSetCalls = failingMaximumLevelSetCalls
        self.maximumLevelChangeGate = maximumLevelChangeGate
        self.syncMode = syncMode
        self.schemaVersion = schemaVersion
    }

    func snapshot() async throws -> ProgressEnvelope {
        snapshotCalls += 1
        if failingSnapshotCalls.remove(snapshotCalls) != nil {
            throw ProgressRepositoryError.failed("test snapshot failure")
        }
        return ProgressEnvelope(
            schemaVersion: schemaVersion,
            actorID: "test-device",
            aggregate: aggregate,
            maximumLeitnerLevel: maximumLeitnerLevel
        )
    }

    func setMaximumLeitnerLevel(_ maximum: Int) async throws -> ProgressEnvelope {
        guard (1...7).contains(maximum) else { throw ProgressRepositoryError.invalidOperation }
        maximumLevelSetCalls += 1
        maximumLevelSetLevels.append(maximum)
        if failingMaximumLevelSetCalls.remove(maximumLevelSetCalls) != nil {
            throw ProgressRepositoryError.failed("test maximum level failure")
        }
        if let maximumLevelChangeGate {
            await maximumLevelChangeGate.pause()
        }
        maximumLeitnerLevel = maximum
        schemaVersion = ProgressEnvelope.currentSchemaVersion
        return ProgressEnvelope(
            schemaVersion: schemaVersion,
            actorID: "test-device",
            aggregate: aggregate,
            maximumLeitnerLevel: maximumLeitnerLevel
        )
    }

    func progressSnapshots() async -> AsyncStream<ProgressEnvelope> {
        let stream = AsyncStream<ProgressEnvelope>.makeStream(of: ProgressEnvelope.self)
        snapshotContinuation = stream.continuation
        snapshotStreamReady = true
        for waiter in streamReadyWaiters { waiter.resume() }
        streamReadyWaiters.removeAll()
        return stream.stream
    }

    func syncStatusEvents() async -> AsyncStream<SyncStatusEvent> {
        let stream = AsyncStream<SyncStatusEvent>.makeStream(of: SyncStatusEvent.self)
        statusContinuation = stream.continuation
        statusStreamReady = true
        for waiter in statusStreamReadyWaiters { waiter.resume() }
        statusStreamReadyWaiters.removeAll()
        return stream.stream
    }

    func waitForSnapshotStream() async {
        if snapshotStreamReady { return }
        await withCheckedContinuation { streamReadyWaiters.append($0) }
    }

    func waitForStatusStream() async {
        if statusStreamReady { return }
        await withCheckedContinuation { statusStreamReadyWaiters.append($0) }
    }

    func emitRemote(_ aggregate: AggregateSnapshot) {
        snapshotContinuation?.yield(ProgressEnvelope(
            schemaVersion: ProgressEnvelope.currentSchemaVersion,
            actorID: "remote-device",
            aggregate: aggregate,
            maximumLeitnerLevel: maximumLeitnerLevel
        ))
    }

    func emitStatus(_ status: SyncStatusEvent) {
        statusContinuation?.yield(status)
    }

    func save(_ session: SessionDetail) async throws -> ProgressOperation {
        saveCalls += 1
        aggregate.sessionsTotal += 1
        aggregate.answered += session.answers.count
        aggregate.correct += session.answers.filter(\.correct).count
        return ProgressOperation.newIntent(session: session)
    }

    func queueIssue(_ issue: QuestionIssue) async throws -> QuestionIssue { issue }

    func synchronize() async throws { synchronizeCalls += 1 }

    func snapshotCallCount() -> Int { snapshotCalls }
    func saveCallCount() -> Int { saveCalls }
    func synchronizeCallCount() -> Int { synchronizeCalls }
    func maximumLevelSetCallCount() -> Int { maximumLevelSetCalls }
    func maximumLevelSetHistory() -> [Int] { maximumLevelSetLevels }
}

/// Holds the first `setMaximumLeitnerLevel` call open so a test can request a
/// second change while the first is in flight. Later calls pass straight
/// through, and `release()` lets the held call finish.
actor MaximumLevelChangeGate {
    private var paused = false
    private var didPause = false
    private var released = false
    private var pauseWaiter: CheckedContinuation<Void, Never>?
    private var releaseWaiter: CheckedContinuation<Void, Never>?

    func pause() async {
        if didPause { return }
        didPause = true
        paused = true
        let waiter = pauseWaiter
        pauseWaiter = nil
        waiter?.resume()
        if released { return }
        await withCheckedContinuation { releaseWaiter = $0 }
    }

    func waitUntilPaused() async {
        if paused { return }
        await withCheckedContinuation { pauseWaiter = $0 }
    }

    func release() {
        released = true
        releaseWaiter?.resume()
        releaseWaiter = nil
    }
}
