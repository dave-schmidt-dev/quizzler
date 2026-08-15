import XCTest
@testable import QuizzleriOS
import QuizzlerKit

@MainActor
final class QuestionShellTests: XCTestCase {
    func testLaunchpadHasSixApprovedStates() {
        XCTAssertEqual(Set(LaunchpadState.allCases), Set([.today, .question, .feedback, .results, .progress, .settings]))
    }

    func testLaunchpadPersistentNavigationUsesLockedThreeDestinations() {
        XCTAssertEqual(LaunchpadState.primaryNavigationStates, [.today, .progress, .settings])
    }

    func testSeededDataCoversEveryRenderer() {
#if DEBUG
        XCTAssertEqual(Set(SeededStudyData.questions.map { $0.question.type }), Set(QuestionType.allCases))
#else
        XCTAssertEqual(Set(SeededStudyData.questions.map { $0.question.type }), Set([.multipleChoice, .scenarioMultipleChoice, .multipleSelect]))
#endif
    }

    func testQuestionIdentityAndReportRemainAvailableForFeedback() {
        let question = SeededStudyData.questions[0]
        XCTAssertFalse(question.qid.isEmpty)
        let context = ReportQuestionContext(identity: question.identity, qid: question.qid, questionType: question.question.type, course: question.courseTitle, appVersion: "1.0.0", build: "100", selectedResponse: "Network segmentation")
        XCTAssertEqual(context.qid, question.qid)
        XCTAssertEqual(context.identity, question.identity)
        XCTAssertEqual(context.type, question.question.type.rawValue)
        XCTAssertEqual(context.build, "100")
        XCTAssertEqual(context.selectedResponse, "Network segmentation")
    }

    func testReportContextTrimsOptionalSelectedResponseWithoutLosingIdentity() {
        let question = SeededStudyData.questions[0]
        let context = ReportQuestionContext(
            identity: question.identity,
            qid: question.qid,
            questionType: question.question.type,
            course: question.courseTitle,
            appVersion: "1.0.0",
            build: "100",
            selectedResponse: "  Network segmentation  "
        )
        XCTAssertEqual(context.selectedResponse, "Network segmentation")
        XCTAssertEqual(context.identity.courseID, SeededStudyData.courseID)
        XCTAssertEqual(context.identity.packID, SeededStudyData.packID)
    }

    func testSelectionCorrectnessContracts() {
        let multipleChoice = SeededStudyData.questions[0].question
        XCTAssertEqual(QuestionShellView.correctAnswer(for: multipleChoice, selection: .single(0)), true)
        XCTAssertEqual(QuestionShellView.correctAnswer(for: multipleChoice, selection: .single(1)), false)
        let multipleSelect = SeededStudyData.questions[2].question
        XCTAssertEqual(QuestionShellView.correctAnswer(for: multipleSelect, selection: .multiple([0, 1])), true)
    }

    func testSelectionCorrectnessCoversEveryQuestionType() {
        let scenario = SeededStudyData.questions[1].question
        XCTAssertTrue(QuestionShellView.correctAnswer(for: scenario, selection: .single(0)))

        let trueFalse = SeededStudyData.questions[3].question
        XCTAssertTrue(QuestionShellView.correctAnswer(for: trueFalse, selection: .boolean(false)))

        let matching = SeededStudyData.questions[4].question
        XCTAssertTrue(QuestionShellView.correctAnswer(for: matching, selection: .matching([0, 1, 2])))
        XCTAssertFalse(QuestionShellView.correctAnswer(for: matching, selection: .matching([-1, 1, 2])))
    }

    func testIncompleteMatchingSelectionRemainsEmptyUntilEveryPairIsChosen() {
        XCTAssertTrue(QuestionSelection.matching([-1, 1, 2]).isEmpty)
        XCTAssertFalse(QuestionSelection.matching([0, 1, 2]).isEmpty)
    }

    func testLaunchpadProgressPersistsAnswersAcrossModelReload() async throws {
        let fileURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("QuizzleriOSTests-\(UUID().uuidString)", isDirectory: false)
        addTeardownBlock {
            try? FileManager.default.removeItem(at: fileURL)
        }

        let identity = QuestionIdentity(courseID: "course", packID: "pack", questionID: "q-1")
        let repository = ProgressRepository(
            actorID: "test-device",
            store: LocalProgressStore(fileURL: fileURL)
        )
        let model = LaunchpadProgressModel(repository: repository)
        model.load()
        try await waitForProgressState(.local, in: model)

        model.record(SessionAnswer(identity: identity, correct: true))
        XCTAssertEqual(model.persistenceState, .local)
        XCTAssertEqual(model.aggregate, AggregateSnapshot())
        model.saveCurrentSession()
        try await waitForProgressState(.local, in: model)

        XCTAssertEqual(model.aggregate, AggregateSnapshot(sessionsTotal: 1, answered: 1, correct: 1))
        XCTAssertTrue(model.unsavedAnswers.isEmpty)

        let reloaded = LaunchpadProgressModel(repository: ProgressRepository(
            actorID: "test-device",
            store: LocalProgressStore(fileURL: fileURL)
        ))
        reloaded.load()
        try await waitForProgressState(.local, in: reloaded)

        XCTAssertEqual(reloaded.aggregate, AggregateSnapshot(sessionsTotal: 1, answered: 1, correct: 1))
        XCTAssertTrue(reloaded.unsavedAnswers.isEmpty)
    }

    func testLaunchpadProgressExposesLocalSavingStateInsteadOfSharedSyncState() async throws {
        let fileURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("QuizzleriOSTests-\(UUID().uuidString)", isDirectory: false)
        addTeardownBlock {
            try? FileManager.default.removeItem(at: fileURL)
        }

        let repository = ProgressRepository(
            actorID: "test-device",
            store: LocalProgressStore(fileURL: fileURL)
        )
        let model = LaunchpadProgressModel(repository: repository)
        model.load()
        try await waitForProgressState(.local, in: model)
        model.record(SessionAnswer(courseID: "course", packID: "pack", questionID: "q-1", correct: false))
        model.saveCurrentSession()

        XCTAssertEqual(model.persistenceState, .saving)
        XCTAssertEqual(model.answered, 1)
        XCTAssertEqual(model.correct, 0)
        try await waitForProgressState(.local, in: model)
    }

    func testLaunchpadProgressPersistsAnswersAddedDuringAnInFlightSave() async throws {
        let fileURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("QuizzleriOSTests-\(UUID().uuidString)", isDirectory: false)
        addTeardownBlock {
            try? FileManager.default.removeItem(at: fileURL)
        }
        let gate = SaveGate()
        let repository = ProgressRepository(
            actorID: "test-device",
            store: LocalProgressStore(fileURL: fileURL)
        )
        let model = LaunchpadProgressModel(repository: repository, beforeSave: {
            await gate.pause()
        })
        model.load()
        try await waitForProgressState(.local, in: model)

        model.record(SessionAnswer(courseID: "course", packID: "pack", questionID: "q-1", correct: true))
        model.saveCurrentSession()
        await gate.waitUntilPaused()
        model.record(SessionAnswer(courseID: "course", packID: "pack", questionID: "q-2", correct: false))
        await gate.release()
        try await waitForProgressState(.local, in: model)

        XCTAssertEqual(model.aggregate, AggregateSnapshot(sessionsTotal: 2, answered: 2, correct: 1))
        XCTAssertTrue(model.unsavedAnswers.isEmpty)
    }

    func testLaunchpadProgressRetryReloadsAfterInitialSnapshotFailure() async throws {
        let repository = ControlledProgressRepository(failingSnapshotCalls: [1])
        let model = LaunchpadProgressModel(repository: repository)

        model.load()
        try await waitForProgressState(.saveFailed, in: model)
        XCTAssertTrue(model.unsavedAnswers.isEmpty)

        model.saveCurrentSession()
        try await waitForProgressState(.local, in: model)

        let snapshotCalls = await repository.snapshotCallCount()
        XCTAssertEqual(snapshotCalls, 2)
        XCTAssertEqual(model.aggregate, AggregateSnapshot())
    }

    func testLaunchpadProgressDoesNotResaveBatchWhenPostSaveSnapshotFails() async throws {
        let repository = ControlledProgressRepository(failingSnapshotCalls: [2])
        let model = LaunchpadProgressModel(repository: repository)
        let answer = SessionAnswer(courseID: "course", packID: "pack", questionID: "q-1", correct: true)

        model.load()
        try await waitForProgressState(.local, in: model)
        model.record(answer)
        model.saveCurrentSession()
        try await waitForProgressState(.saveFailed, in: model)

        XCTAssertTrue(model.unsavedAnswers.isEmpty)
        let saveCallsBeforeRetry = await repository.saveCallCount()
        XCTAssertEqual(saveCallsBeforeRetry, 1)

        model.saveCurrentSession()
        try await waitForProgressState(.local, in: model)

        let saveCallsAfterRetry = await repository.saveCallCount()
        XCTAssertEqual(saveCallsAfterRetry, 1)
        XCTAssertEqual(model.aggregate, AggregateSnapshot(sessionsTotal: 1, answered: 1, correct: 1))
    }

    func testQuestionShellUsesTheInjectedRepositoryForReports() {
        let repository = ProgressRepository(actorID: "test-device")
        let shell = QuestionShellView(
            seededQuestion: SeededStudyData.questions[0],
            phase: .question,
            repository: repository,
            selection: .constant(.none),
            onCheck: { _ in },
            onFinish: {}
        )
        XCTAssertTrue(shell.repository === repository)
    }

    private func waitForProgressState(
        _ expected: LaunchpadProgressModel.PersistenceState,
        in model: LaunchpadProgressModel
    ) async throws {
        for _ in 0..<100 {
            if model.persistenceState == expected { return }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTFail("Timed out waiting for progress state \(expected)")
    }
}

private actor ControlledProgressRepository: LaunchpadProgressRepository {
    private var aggregate = AggregateSnapshot()
    private var snapshotCalls = 0
    private var saveCalls = 0
    private var failingSnapshotCalls: Set<Int>

    init(failingSnapshotCalls: Set<Int>) {
        self.failingSnapshotCalls = failingSnapshotCalls
    }

    func snapshot() async throws -> ProgressEnvelope {
        snapshotCalls += 1
        if failingSnapshotCalls.remove(snapshotCalls) != nil {
            throw ProgressRepositoryError.failed("test snapshot failure")
        }
        return ProgressEnvelope(actorID: "test-device", aggregate: aggregate)
    }

    func save(_ session: SessionDetail) async throws -> ProgressOperation {
        saveCalls += 1
        aggregate.sessionsTotal += 1
        aggregate.answered += session.answers.count
        aggregate.correct += session.answers.filter(\.correct).count
        return ProgressOperation.newIntent(session: session)
    }

    func snapshotCallCount() -> Int { snapshotCalls }
    func saveCallCount() -> Int { saveCalls }
}

private actor SaveGate {
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
