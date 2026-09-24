import XCTest
import CryptoKit
import UIKit
import SwiftUI
@testable import QuizzleriOS
import QuizzlerKit

@MainActor
final class QuestionShellTests: XCTestCase {
    func testLeitnerScheduleIntervalsAndLevelTransitions() {
        XCTAssertEqual((1...7).map(LeitnerSchedule.intervalLabel(for:)), [
            "1 day", "3 days", "7 days", "14 days", "30 days", "60 days", "120 days"
        ])
        XCTAssertEqual(LeitnerSchedule.defaultMaximumLevel, 5)
        XCTAssertEqual(LeitnerSchedule.nextLevel(current: nil, correct: true, maximum: 5), 2)
        XCTAssertEqual(LeitnerSchedule.nextLevel(current: 4, correct: false, maximum: 5), 2)
        XCTAssertEqual(LeitnerSchedule.nextLevel(current: 1, correct: false, maximum: 5), 1)
        XCTAssertEqual(LeitnerSchedule.nextLevel(current: 5, correct: true, maximum: 5), 5)
        XCTAssertEqual(LeitnerSchedule.nextLevel(current: 7, correct: true, maximum: 3), 3)
    }

    func testColdLaunchStingPolicyOnlyPresentsForOrdinaryColdStartOrExplicitUITest() {
        func presents(
            probe: Bool = false,
            fixture: Bool = false,
            underTest: Bool = false,
            optedIn: Bool = false,
            destination: Bool = false
        ) -> Bool {
            ColdLaunchStingPolicy.shouldPresent(
                isDevelopmentProbe: probe,
                isExistingUITestFixture: fixture,
                isRunningUnderXCTest: underTest,
                hasOptedInForUITest: optedIn,
                hasDestination: destination
            )
        }

        XCTAssertTrue(presents())
        XCTAssertTrue(presents(underTest: true, optedIn: true))
        XCTAssertFalse(presents(probe: true))
        XCTAssertFalse(presents(fixture: true))
        XCTAssertFalse(presents(destination: true))
        XCTAssertFalse(presents(underTest: true))
        XCTAssertFalse(presents(probe: true, underTest: true, optedIn: true))
        XCTAssertFalse(presents(fixture: true, underTest: true, optedIn: true))
        XCTAssertFalse(presents(underTest: true, optedIn: true, destination: true))
    }

    func testAppDelegateRegistersForRemoteNotificationsOnceAtLaunch() {
        let registration = RegistrationRecorder()
        let delegate = QuizzlerAppDelegate(
            registerForRemoteNotifications: { registration.record() }
        )

        XCTAssertTrue(delegate.application(UIApplication.shared, didFinishLaunchingWithOptions: nil))
        XCTAssertEqual(registration.count, 1)

        XCTAssertTrue(delegate.application(UIApplication.shared, didFinishLaunchingWithOptions: nil))
        XCTAssertEqual(registration.count, 1)
    }

    func testLaunchpadHasSixApprovedStates() {
        XCTAssertEqual(Set(LaunchpadState.allCases), Set([.today, .question, .feedback, .results, .progress, .settings]))
    }

    func testLaunchpadPersistentNavigationUsesLockedThreeDestinations() {
        XCTAssertEqual(LaunchpadState.primaryNavigationStates, [.today, .progress, .settings])
    }

    /// The tab bar renders these directly, and `AccessibilityUITests` asserts
    /// the three labels as buttons. Pinning them here fails in seconds rather
    /// than in a simulator run.
    func testPersistentDestinationsCarryTheAssertedLabelsAndIcons() {
        XCTAssertEqual(LaunchpadState.primaryNavigationStates.map(\.title), ["Today", "Progress", "Settings"])
        XCTAssertEqual(
            LaunchpadState.primaryNavigationStates.map(\.icon),
            ["sun.max", "chart.line.uptrend.xyaxis", "gearshape"]
        )
    }

    func testPreviewFixtureCoversEveryRendererAndIsDebugOnly() {
        // The fixture exists so all five renderers can be exercised without an
        // installed pack. It is compiled out of Release entirely (the file name
        // matches EXCLUDED_SOURCE_FILE_NAMES), so this assertion is Debug-only
        // by construction rather than by branching on the configuration.
        XCTAssertEqual(Set(SeededStudyData.questions.map { $0.question.type }), Set(QuestionType.allCases))
    }

    func testQuestionIdentityAndReportRemainAvailableForFeedback() {
        let question = SeededStudyData.questions[0]
        XCTAssertFalse(question.qid.isEmpty)
        let context = ReportQuestionContext(identity: question.identity, qid: question.qid, questionType: question.question.type, appVersion: "1.0.0", build: "100", selectedResponse: "Network segmentation")
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
            appVersion: "1.0.0",
            build: "100",
            selectedResponse: "  Network segmentation  "
        )
        XCTAssertEqual(context.selectedResponse, "Network segmentation")
        XCTAssertEqual(context.identity.courseID, SeededStudyData.courseID)
        XCTAssertEqual(context.identity.packID, SeededStudyData.packID)
    }

    func testStudyResumePositionIsPerPackAndDoesNotUseSharedAggregateCounters() {
        let suiteName = "QuizzleriOSTests-study-resume-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suiteName)!
        defer { defaults.removePersistentDomain(forName: suiteName) }

        StudyResumePosition.store(7, courseID: "cysa", packID: "core", questionCount: 6, defaults: defaults)
        StudyResumePosition.store(2, courseID: "cissp", packID: "core", questionCount: 6, defaults: defaults)

        XCTAssertEqual(StudyResumePosition.index(courseID: "cysa", packID: "core", questionCount: 6, defaults: defaults), 1)
        XCTAssertEqual(StudyResumePosition.index(courseID: "cissp", packID: "core", questionCount: 6, defaults: defaults), 2)
    }

    func testQueueingReportSchedulesCloudSynchronizationAfterLocalPersistence() async throws {
        let repository = ControlledProgressRepository(syncMode: .cloudKit)
        let issue = try QuestionIssue(
            issueID: "issue-test",
            courseID: "course",
            packID: "pack",
            questionID: "question",
            questionType: .multipleChoice,
            appVersion: "1.0",
            build: "1",
            description: "Typo"
        )

        let queuedIssue = try await repository.queueIssueAndScheduleSync(issue)
        XCTAssertEqual(queuedIssue, issue)
        for _ in 0..<100 {
            if await repository.synchronizeCallCount() == 1 { return }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTFail("Expected the queued CloudKit report to start synchronization")
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

    func testLaunchpadProgressPersistsAnswerWhenFeedbackAppears() async throws {
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

        model.recordAndSave(SessionAnswer(courseID: "course", packID: "pack", questionID: "q-feedback", correct: true))
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
        // A second Next-question tap while the first write is paused must
        // queue this answer, not launch a competing prefix removal.
        model.saveCurrentSession()
        await gate.release()
        try await waitForProgressState(.local, in: model)

        XCTAssertEqual(model.aggregate, AggregateSnapshot(sessionsTotal: 2, answered: 2, correct: 1))
        XCTAssertTrue(model.unsavedAnswers.isEmpty)
    }

    func testLaunchpadProgressScopesCountersToTheSelectedPack() async throws {
        let fileURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("QuizzleriOSTests-\(UUID().uuidString)", isDirectory: false)
        addTeardownBlock { try? FileManager.default.removeItem(at: fileURL) }
        let repository = ProgressRepository(
            actorID: "test-device",
            store: LocalProgressStore(fileURL: fileURL)
        )
        _ = try await repository.save(SessionDetail(answers: [
            SessionAnswer(courseID: "alpha", packID: "core", questionID: "q-1", correct: true),
            SessionAnswer(courseID: "alpha", packID: "core", questionID: "q-2", correct: false)
        ]))
        _ = try await repository.save(SessionDetail(answers: [
            SessionAnswer(courseID: "beta", packID: "core", questionID: "q-1", correct: true)
        ]))

        let model = LaunchpadProgressModel(repository: repository)
        model.load()
        try await waitForProgressState(.local, in: model)

        XCTAssertEqual(
            model.aggregate(courseID: "alpha", packID: "core"),
            AggregateSnapshot(answered: 2, correct: 1)
        )
        XCTAssertEqual(
            model.aggregate(courseID: "beta", packID: "core"),
            AggregateSnapshot(answered: 1, correct: 1)
        )
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
        XCTAssertEqual(
            snapshotCalls,
            3,
            "retry reads the initial snapshot and its post-migration readiness snapshot"
        )
        XCTAssertEqual(model.aggregate, AggregateSnapshot())
    }

    func testCloudStartupMigratesLegacySchemaBeforeMarkingStudyReady() async throws {
        let repository = ControlledProgressRepository(syncMode: .cloudKit, schemaVersion: 1)
        let model = LaunchpadProgressModel(repository: repository)

        model.load()
        try await waitForProgressState(.synced, in: model)

        XCTAssertTrue(model.isReadyForStudy)
        XCTAssertEqual(model.envelope?.schemaVersion, ProgressEnvelope.currentSchemaVersion)
        XCTAssertEqual(model.maximumLeitnerLevel, LeitnerSchedule.defaultMaximumLevel)
        let synchronizeCalls = await repository.synchronizeCallCount()
        XCTAssertEqual(
            synchronizeCalls,
            2,
            "v1 startup fetches the authoritative baseline, then syncs the cap migration"
        )
        let migrationCalls = await repository.maximumLevelSetCallCount()
        XCTAssertEqual(migrationCalls, 1)
    }

    func testForegroundActivationRequestsCloudSynchronization() async throws {
        let repository = ControlledProgressRepository(syncMode: .cloudKit)
        let model = LaunchpadProgressModel(repository: repository)

        model.load()
        try await waitForProgressState(.synced, in: model)
        let initialSynchronizeCalls = await repository.synchronizeCallCount()
        XCTAssertEqual(initialSynchronizeCalls, 1)

        model.synchronizeOnForeground()
        try await waitForProgressState(.synced, in: model)

        let foregroundSynchronizeCalls = await repository.synchronizeCallCount()
        XCTAssertEqual(foregroundSynchronizeCalls, 2)
    }

    func testAccountChangeIsShownAsAProtectedStateInsteadOfRetryableSync() async throws {
        let repository = ControlledProgressRepository(syncMode: .cloudKit)
        let model = LaunchpadProgressModel(repository: repository)

        model.load()
        try await waitForProgressState(.synced, in: model)
        await repository.waitForStatusStream()
        await repository.emitStatus(.init(state: .accountIsolationRequired, reason: .accountChanged))
        try await waitForProgressState(.accountChanged, in: model)
    }

    func testLaunchpadProgressAppliesRemoteSnapshotStream() async throws {
        let repository = ControlledProgressRepository(syncMode: .cloudKit)
        let model = LaunchpadProgressModel(repository: repository)

        model.load()
        try await waitForProgressState(.synced, in: model)
        await repository.waitForSnapshotStream()
        await repository.emitRemote(AggregateSnapshot(sessionsTotal: 4, answered: 4, correct: 3))

        for _ in 0..<100 {
            if model.aggregate == AggregateSnapshot(sessionsTotal: 4, answered: 4, correct: 3) { return }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTFail("timed out waiting for remote progress snapshot")
    }

    func testLaunchpadProgressDoesNotResaveBatchWhenPostSaveSnapshotFails() async throws {
        // Startup reads twice before enabling study, so call 3 is the read
        // after saving the answer.
        let repository = ControlledProgressRepository(failingSnapshotCalls: [3])
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

    /// The defect this covers: a wrong answer kept the same selected treatment
    /// as a right one and no option was ever named correct, so the screen never
    /// told the learner which answer they should have given.
    func testCheckedAnswersNameTheRightOptionAndTheLearnersOwn() {
        // While answering, nothing is revealed: an empty correct set is what the
        // question phase passes, and it must mark no row at all.
        XCTAssertEqual(choiceMarking(index: 0, selected: true, correctIndexes: []), ChoiceMarking.none)
        XCTAssertEqual(choiceMarking(index: 2, selected: false, correctIndexes: []), ChoiceMarking.none)

        // Wrong answer: the chosen row is the learner's, the right row is named.
        XCTAssertEqual(choiceMarking(index: 0, selected: true, correctIndexes: [2]), ChoiceMarking.yourAnswer)
        XCTAssertEqual(choiceMarking(index: 2, selected: false, correctIndexes: [2]), ChoiceMarking.correct)
        XCTAssertEqual(choiceMarking(index: 1, selected: false, correctIndexes: [2]), ChoiceMarking.none)

        // Right answer: the chosen row reads `correct`, never `your answer`.
        XCTAssertEqual(choiceMarking(index: 2, selected: true, correctIndexes: [2]), ChoiceMarking.correct)

        // Multiple select: every right row is named, and a wrong pick alongside
        // a right one still reads as the learner's.
        XCTAssertEqual(choiceMarking(index: 1, selected: true, correctIndexes: [0, 1]), ChoiceMarking.correct)
        XCTAssertEqual(choiceMarking(index: 3, selected: true, correctIndexes: [0, 1]), ChoiceMarking.yourAnswer)

        XCTAssertEqual(ChoiceMarking.correct.caption, "correct")
        XCTAssertEqual(ChoiceMarking.yourAnswer.caption, "your answer")
        XCTAssertNil(ChoiceMarking.none.caption)
    }

    /// Sessions were fixed at ten questions; the length is now a Settings
    /// choice, and a stored value that is not on offer must not be trusted.
    func testSessionLengthClampsToThePackAndRejectsUnofferedValues() {
        XCTAssertEqual(StudySessionLength.limit(stored: 20, packQuestionCount: 203), 20)
        XCTAssertEqual(StudySessionLength.limit(stored: StudySessionLength.wholePack, packQuestionCount: 203), 203)
        // A pack smaller than the chosen length serves the whole pack, not a
        // request for questions that do not exist.
        XCTAssertEqual(StudySessionLength.limit(stored: 40, packQuestionCount: 6), 6)
        // Hand-edited or corrupted defaults fall back rather than propagate.
        XCTAssertEqual(StudySessionLength.limit(stored: 7, packQuestionCount: 203), StudySessionLength.default)
        XCTAssertEqual(StudySessionLength.limit(stored: -3, packQuestionCount: 203), StudySessionLength.default)
        // An empty pack yields no request at all, which the caller rejects.
        XCTAssertEqual(StudySessionLength.limit(stored: 10, packQuestionCount: 0), 0)

        XCTAssertEqual(StudySessionLength.label(10), "10 questions")
        XCTAssertEqual(StudySessionLength.label(StudySessionLength.wholePack), "Whole pack")
        XCTAssertEqual(StudySessionLength.maximumLabel(10), "Up to 10 questions")
        XCTAssertEqual(StudySessionLength.options.first, StudySessionLength.default)
    }

    func testTodayOverrideLeavesStoredDefaultAndCapsEveryStartType() {
        let storedDefault = 20
        let nextSessionOverride = 10

        XCTAssertEqual(
            StudySessionLength.effective(stored: storedDefault, nextSessionOverride: nextSessionOverride),
            nextSessionOverride
        )
        XCTAssertEqual(
            StudySessionLength.limit(stored: storedDefault, nextSessionOverride: nextSessionOverride, candidateCount: 45),
            10,
            "normal, scheduled, and retry starts use the same candidate cap"
        )
        XCTAssertEqual(
            StudySessionLength.effective(stored: storedDefault, nextSessionOverride: nil),
            storedDefault,
            "clearing the temporary Today choice restores the saved Settings default"
        )
        XCTAssertEqual(
            StudySessionLength.effective(stored: -1, nextSessionOverride: nil),
            StudySessionLength.default,
            "an invalid stored preference uses the documented default"
        )
        XCTAssertEqual(StudyScheduledReview.default, true)
    }

    /// The counter is one-based and names the session length, not the pack.
    func testSessionPositionCountsFromOne() {
        XCTAssertEqual(SessionPosition(index: 0, count: 10).label, "1 of 10")
        XCTAssertEqual(SessionPosition(index: 9, count: 10).label, "10 of 10")
        XCTAssertEqual(SessionPosition(index: 0, count: 10).displayLabel, "Question 1 of 10")
    }

    func testQuestionShellUsesTheInjectedRepositoryForReports() {
        let repository = ProgressRepository(actorID: "test-device")
        let shell = QuestionShellView(
            studyQuestion: SeededStudyData.questions[0],
            phase: .question,
            repository: repository,
            selection: .constant(.none),
            onCheck: { _ in },
            onFinish: {}
        )
        XCTAssertEqual(shell.repository.syncMode, .local)
    }

    func testSessionPositionDisplayLabelAndFraction() {
        let first = SessionPosition(index: 0, count: 10)
        XCTAssertEqual(first.displayLabel, "Question 1 of 10")
        XCTAssertEqual(first.fraction, 0.1, accuracy: 1e-9)

        let last = SessionPosition(index: 9, count: 10)
        XCTAssertEqual(last.displayLabel, "Question 10 of 10")
        XCTAssertEqual(last.fraction, 1.0, accuracy: 1e-9)

        let mid = SessionPosition(index: 2, count: 10)
        XCTAssertEqual(mid.displayLabel, "Question 3 of 10")
        XCTAssertEqual(mid.fraction, 0.3, accuracy: 1e-9)
    }

    func testAnswersOnTapForEveryQuestionType() {
        // Single-answer types commit immediately on tap.
        XCTAssertTrue(QuestionShellView.answersOnTap(.multipleChoice))
        XCTAssertTrue(QuestionShellView.answersOnTap(.scenarioMultipleChoice))
        XCTAssertTrue(QuestionShellView.answersOnTap(.trueFalse))
        // Multi-step types need an explicit Check Answer press.
        XCTAssertFalse(QuestionShellView.answersOnTap(.multipleSelect))
        XCTAssertFalse(QuestionShellView.answersOnTap(.matching))
    }

    func testReportOptionsForEveryQuestionType() {
        let questions = SeededStudyData.questions
        let mc = questions.first { $0.question.type == .multipleChoice }!
        let smc = questions.first { $0.question.type == .scenarioMultipleChoice }!
        let ms = questions.first { $0.question.type == .multipleSelect }!
        let tf = questions.first { $0.question.type == .trueFalse }!
        let matching = questions.first { $0.question.type == .matching }!

        // MC and SMC return their option array.
        if case .multipleChoice(let q) = mc.question {
            XCTAssertEqual(QuestionShellView.reportOptions(for: mc.question), q.options)
        }
        if case .scenarioMultipleChoice(let q) = smc.question {
            XCTAssertEqual(QuestionShellView.reportOptions(for: smc.question), q.options)
        }
        // Multiple select returns its option array.
        if case .multipleSelect(let q) = ms.question {
            XCTAssertEqual(QuestionShellView.reportOptions(for: ms.question), q.options)
        }
        // True/false always returns exactly ["True", "False"].
        XCTAssertEqual(QuestionShellView.reportOptions(for: tf.question), ["True", "False"])
        // Matching returns an empty array (no picker offered).
        XCTAssertEqual(QuestionShellView.reportOptions(for: matching.question), [])
    }

    func testActiveSessionAdvancedMidSession() {
        let questions = SeededStudyData.questions
        var session = ActiveSession(mode: .normal, questions: questions, position: 0, answers: [])
        let advanced = session.advanced()
        XCTAssertNotNil(advanced)
        XCTAssertEqual(advanced?.position, 1)
        // Original session is unmodified.
        XCTAssertEqual(session.position, 0)
    }

    func testActiveSessionAdvancedAtLastQuestion() {
        let questions = Array(SeededStudyData.questions.prefix(3))
        let session = ActiveSession(mode: .normal, questions: questions, position: 2, answers: [])
        XCTAssertNil(session.advanced(), "advanced() must return nil when the session is exhausted")
    }

    func testActiveSessionAdvancedPreservesNewIdentities() {
        let questions = SeededStudyData.questions
        let newIds: Set<QuestionIdentity> = [questions[0].identity, questions[1].identity]
        let session = ActiveSession(mode: .normal, questions: questions, position: 0, answers: [], newIdentities: newIds)
        let advanced = session.advanced()
        XCTAssertEqual(advanced?.newIdentities, newIds)
    }

    // MARK: - SessionSummary Tests

    func testSessionSummaryMixedSession() {
        let questions = Array(SeededStudyData.questions.prefix(3))
        let q0 = questions[0]
        let q1 = questions[1]
        let q2 = questions[2]

        let answers = [
            SessionAnswer(identity: q0.identity, correct: true),
            SessionAnswer(identity: q1.identity, correct: false),
            SessionAnswer(identity: q2.identity, correct: true)
        ]
        let newIdentities: Set<QuestionIdentity> = [q0.identity, q1.identity]
        let session = ActiveSession(
            mode: .normal,
            questions: questions,
            position: 2,
            answers: answers,
            newIdentities: newIdentities
        )

        let summary = SessionSummary(session: session)
        XCTAssertEqual(summary.answered, 3)
        XCTAssertEqual(summary.right, 2)
        XCTAssertEqual(summary.newLearned, 1)
        XCTAssertEqual(summary.toRetry, 1)
        XCTAssertEqual(summary.missedPrompts, [q1.prompt])
    }

    func testSessionSummaryAllRight() {
        let questions = Array(SeededStudyData.questions.prefix(3))
        let answers = questions.map { SessionAnswer(identity: $0.identity, correct: true) }
        let newIdentities = Set(questions.map(\.identity))
        let session = ActiveSession(
            mode: .normal,
            questions: questions,
            position: 2,
            answers: answers,
            newIdentities: newIdentities
        )

        let summary = SessionSummary(session: session)
        XCTAssertEqual(summary.answered, 3)
        XCTAssertEqual(summary.right, 3)
        XCTAssertEqual(summary.newLearned, 3)
        XCTAssertEqual(summary.toRetry, 0)
        XCTAssertEqual(summary.missedPrompts, [])
    }

    func testSessionSummaryRepeatedIdentity() {
        let questions = Array(SeededStudyData.questions.prefix(2))
        let q0 = questions[0]
        let q1 = questions[1]

        let answers = [
            SessionAnswer(identity: q0.identity, correct: false),
            SessionAnswer(identity: q1.identity, correct: false),
            SessionAnswer(identity: q0.identity, correct: false),
            SessionAnswer(identity: q0.identity, correct: true),
            SessionAnswer(identity: q0.identity, correct: true)
        ]
        let session = ActiveSession(
            mode: .normal,
            questions: questions,
            position: 4,
            answers: answers,
            newIdentities: [q0.identity]
        )

        let summary = SessionSummary(session: session)
        XCTAssertEqual(summary.answered, 5)
        XCTAssertEqual(summary.right, 2)
        XCTAssertEqual(summary.newLearned, 1)
        XCTAssertEqual(summary.toRetry, 2)
        XCTAssertEqual(summary.missedPrompts, [q0.prompt, q1.prompt])
    }

    func testSessionSummaryNewVsPreviouslySeen() {
        let questions = Array(SeededStudyData.questions.prefix(3))
        let qNew1 = questions[0]
        let qNew2 = questions[1]
        let qSeen = questions[2]

        let newIdentities: Set<QuestionIdentity> = [qNew1.identity, qNew2.identity]

        // Case A: qNew1 correct, qNew2 wrong, qSeen correct
        let answersA = [
            SessionAnswer(identity: qNew1.identity, correct: true),
            SessionAnswer(identity: qNew2.identity, correct: false),
            SessionAnswer(identity: qSeen.identity, correct: true)
        ]
        let sessionA = ActiveSession(
            mode: .normal,
            questions: questions,
            position: 2,
            answers: answersA,
            newIdentities: newIdentities
        )
        let summaryA = SessionSummary(session: sessionA)
        XCTAssertEqual(summaryA.answered, 3)
        XCTAssertEqual(summaryA.right, 2)
        XCTAssertEqual(summaryA.newLearned, 1)
        XCTAssertEqual(summaryA.toRetry, 1)
        XCTAssertEqual(summaryA.missedPrompts, [qNew2.prompt])

        // Case B: all correct, but only new questions contribute to newLearned
        let answersB = [
            SessionAnswer(identity: qNew1.identity, correct: true),
            SessionAnswer(identity: qNew2.identity, correct: true),
            SessionAnswer(identity: qSeen.identity, correct: true)
        ]
        let sessionB = ActiveSession(
            mode: .normal,
            questions: questions,
            position: 2,
            answers: answersB,
            newIdentities: newIdentities
        )
        let summaryB = SessionSummary(session: sessionB)
        XCTAssertEqual(summaryB.answered, 3)
        XCTAssertEqual(summaryB.right, 3)
        XCTAssertEqual(summaryB.newLearned, 2)
        XCTAssertEqual(summaryB.toRetry, 0)
        XCTAssertEqual(summaryB.missedPrompts, [])
    }

    func testCloudRuntimeMigratesRetainedLocalProgressOnceBeforeSyncing() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("QuizzleriOSCloudRuntime-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: directory) }

        let legacyStore = LocalProgressStore(fileURL: directory.appendingPathComponent("progress-v1.json"))
        let legacy = ProgressRepository(actorID: "legacy-device", store: legacyStore)
        let session = SessionDetail(
            sessionID: "legacy-session",
            completedAt: Date(timeIntervalSince1970: 1_700_000_000),
            answers: [SessionAnswer(courseID: "cysa", packID: "cysa-core", questionID: "q-1", correct: true)]
        )
        _ = try await legacy.save(session, operationID: nil, now: session.completedAt)

        let transport = RuntimeCloudTransport()
        let cloud = try CloudProgressRepository(
            actorID: "device-under-test",
            persistence: CloudProgressMemoryStore(),
            transport: transport
        )
        let runtime = CloudLaunchpadProgressRepository(
            cloud: cloud,
            legacyStore: legacyStore,
            migrationMarkerURL: directory.appendingPathComponent("migration-v1")
        )

        try await runtime.synchronize()
        let firstSnapshot = try await runtime.snapshot()
        let firstSendCount = await transport.progressSendCount()
        XCTAssertEqual(firstSnapshot.aggregate, AggregateSnapshot(sessionsTotal: 1, answered: 1, correct: 1))
        XCTAssertEqual(firstSendCount, 1)

        try await runtime.synchronize()
        let secondSnapshot = try await runtime.snapshot()
        let secondSendCount = await transport.progressSendCount()
        XCTAssertEqual(secondSnapshot.aggregate, AggregateSnapshot(sessionsTotal: 1, answered: 1, correct: 1))
        XCTAssertEqual(secondSendCount, 1)
        XCTAssertTrue(FileManager.default.fileExists(atPath: directory.appendingPathComponent("migration-v1").path))
    }

    func testCloudRuntimeRefusesCompactedLegacyHistoryInsteadOfInventingAnswers() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("QuizzleriOSCloudRuntime-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: directory) }

        let legacyStore = LocalProgressStore(fileURL: directory.appendingPathComponent("progress-v1.json"))
        try await legacyStore.write(ProgressEnvelope(
            actorID: "legacy-device",
            aggregate: AggregateSnapshot(sessionsTotal: 2, answered: 2, correct: 1)
        ))
        let runtime = CloudLaunchpadProgressRepository(
            cloud: try CloudProgressRepository(
                actorID: "device-under-test",
                persistence: CloudProgressMemoryStore(),
                transport: RuntimeCloudTransport()
            ),
            legacyStore: legacyStore,
            migrationMarkerURL: directory.appendingPathComponent("migration-v1")
        )

        do {
            try await runtime.synchronize()
            XCTFail("compacted history must remain local until it can be reconciled")
        } catch let error as CloudLaunchpadProgressError {
            XCTAssertEqual(error, .legacyHistoryIsNotFullyRetained)
        }
        XCTAssertFalse(FileManager.default.fileExists(atPath: directory.appendingPathComponent("migration-v1").path))
    }

    func testCloudRuntimeAuthorizedImportRemovesMarkerOnlyAfterConfirmedSend() async throws {
        let directory = try makeCloudRuntimeDirectory()
        addTeardownBlock { try? FileManager.default.removeItem(at: directory) }
        let legacyURL = directory.appendingPathComponent("progress-v1.json")
        let migrationURL = directory.appendingPathComponent("migration-v1")
        let importURL = directory.appendingPathComponent("cloud-progress-import-authorized-v1.json")
        let legacyStore = LocalProgressStore(fileURL: legacyURL)
        let legacy = ProgressRepository(actorID: "legacy-device", store: legacyStore)
        let session = SessionDetail(
            sessionID: "legacy-session",
            completedAt: Date(timeIntervalSince1970: 1_700_000_000),
            answers: [SessionAnswer(courseID: "cysa", packID: "cysa-core", questionID: "q-1", correct: true)]
        )
        _ = try await legacy.save(session, operationID: nil, now: session.completedAt)
        try writeImportAuthorization(
            to: importURL,
            legacyPayloadURL: legacyURL,
            legacyActorID: "legacy-device"
        )

        let cloudStore = CloudProgressMemoryStore(checkpoint: CloudProgressCheckpoint(
            envelope: ProgressEnvelope(actorID: "device-under-test"),
            snapshotDirty: true,
            requiresRebase: true,
            accountIsolationRequired: true
        ))
        let transport = RuntimeCloudTransport()
        let cloud = try CloudProgressRepository(
            actorID: "device-under-test",
            persistence: cloudStore,
            transport: transport
        )
        let runtime = CloudLaunchpadProgressRepository(
            cloud: cloud,
            legacyStore: legacyStore,
            migrationMarkerURL: migrationURL,
            legacyProgressURL: legacyURL,
            importAuthorizationMarkerURL: importURL
        )

        try await runtime.synchronize()
        let imported = try await runtime.snapshot()
        XCTAssertEqual(imported.aggregate, AggregateSnapshot(sessionsTotal: 1, answered: 1, correct: 1))
        XCTAssertFalse(FileManager.default.fileExists(atPath: importURL.path))
        let sendCount = await transport.progressSendCount()
        XCTAssertEqual(sendCount, 1)
    }

    func testCloudRuntimeAuthorizedImportKeepsMarkerWhenSendFails() async throws {
        let directory = try makeCloudRuntimeDirectory()
        addTeardownBlock { try? FileManager.default.removeItem(at: directory) }
        let legacyURL = directory.appendingPathComponent("progress-v1.json")
        let importURL = directory.appendingPathComponent("cloud-progress-import-authorized-v1.json")
        let legacyStore = LocalProgressStore(fileURL: legacyURL)
        let legacy = ProgressRepository(actorID: "legacy-device", store: legacyStore)
        let session = SessionDetail(
            sessionID: "legacy-session",
            completedAt: Date(timeIntervalSince1970: 1_700_000_000),
            answers: [SessionAnswer(courseID: "cysa", packID: "cysa-core", questionID: "q-1", correct: true)]
        )
        _ = try await legacy.save(session, operationID: nil, now: session.completedAt)
        try writeImportAuthorization(to: importURL, legacyPayloadURL: legacyURL, legacyActorID: "legacy-device")

        let cloud = try CloudProgressRepository(
            actorID: "device-under-test",
            persistence: CloudProgressMemoryStore(checkpoint: CloudProgressCheckpoint(
                envelope: ProgressEnvelope(actorID: "device-under-test"),
                snapshotDirty: true,
                requiresRebase: true,
                accountIsolationRequired: true
            )),
            transport: RuntimeCloudTransport(sendError: .network)
        )
        let runtime = CloudLaunchpadProgressRepository(
            cloud: cloud,
            legacyStore: legacyStore,
            migrationMarkerURL: directory.appendingPathComponent("migration-v1"),
            legacyProgressURL: legacyURL,
            importAuthorizationMarkerURL: importURL
        )

        do {
            try await runtime.synchronize()
            XCTFail("a failed send must not consume the authorization marker")
        } catch {
            XCTAssertTrue(FileManager.default.fileExists(atPath: importURL.path))
        }
    }

    func testCloudRuntimeAuthorizedImportRejectsNonEmptyRemoteBaseline() async throws {
        let directory = try makeCloudRuntimeDirectory()
        addTeardownBlock { try? FileManager.default.removeItem(at: directory) }
        let legacyURL = directory.appendingPathComponent("progress-v1.json")
        let importURL = directory.appendingPathComponent("cloud-progress-import-authorized-v1.json")
        let legacyStore = LocalProgressStore(fileURL: legacyURL)
        let legacy = ProgressRepository(actorID: "legacy-device", store: legacyStore)
        let session = SessionDetail(
            sessionID: "legacy-session",
            completedAt: Date(timeIntervalSince1970: 1_700_000_000),
            answers: [SessionAnswer(courseID: "cysa", packID: "cysa-core", questionID: "q-1", correct: true)]
        )
        _ = try await legacy.save(session, operationID: nil, now: session.completedAt)
        try writeImportAuthorization(to: importURL, legacyPayloadURL: legacyURL, legacyActorID: "legacy-device")
        let remoteSnapshot = try CloudKitMapping.snapshotRecord(
            ProgressEnvelope(
                actorID: "remote-device",
                aggregate: AggregateSnapshot(sessionsTotal: 1, answered: 1, correct: 1)
            )
        )
        let cloud = try CloudProgressRepository(
            actorID: "device-under-test",
            persistence: CloudProgressMemoryStore(checkpoint: CloudProgressCheckpoint(
                envelope: ProgressEnvelope(actorID: "device-under-test"),
                snapshotDirty: true,
                requiresRebase: true,
                accountIsolationRequired: true
            )),
            transport: RuntimeCloudTransport(fetchRecords: [remoteSnapshot])
        )
        let runtime = CloudLaunchpadProgressRepository(
            cloud: cloud,
            legacyStore: legacyStore,
            migrationMarkerURL: directory.appendingPathComponent("migration-v1"),
            legacyProgressURL: legacyURL,
            importAuthorizationMarkerURL: importURL
        )

        do {
            try await runtime.synchronize()
            XCTFail("non-empty remote state must not accept a legacy import")
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .accountIsolationRequired)
        }
        XCTAssertTrue(FileManager.default.fileExists(atPath: importURL.path))
    }

    private func makeCloudRuntimeDirectory() throws -> URL {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("QuizzleriOSCloudRuntime-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory
    }

    private func writeImportAuthorization(
        to markerURL: URL,
        legacyPayloadURL: URL,
        legacyActorID: String
    ) throws {
        let digest = SHA256.hash(data: try Data(contentsOf: legacyPayloadURL))
            .map { String(format: "%02x", $0) }
            .joined()
        let authorization = CloudLaunchpadImportAuthorization(
            version: CloudLaunchpadImportAuthorization.currentVersion,
            nonce: UUID().uuidString,
            legacyActorID: legacyActorID,
            legacyPayloadDigest: digest
        )
        try JSONEncoder().encode(authorization).write(to: markerURL, options: .atomic)
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

    // MARK: - ReportChip tests

    func testReportChipCategoryMapping() {
        XCTAssertEqual(ReportChip.wrongAnswer.category, .incorrectAnswer)
        XCTAssertEqual(ReportChip.confusing.category,   .other)
        XCTAssertEqual(ReportChip.typo.category,        .typo)
        XCTAssertEqual(ReportChip.other.category,       .other)
    }

    func testReportChipDescriptionLabelOnlyWhenNoDetail() {
        for chip in ReportChip.allCases {
            let result = ReportChip.description(chip: chip, proposed: nil, detail: "")
            XCTAssertEqual(result, chip.label, "chip \(chip) with no detail must equal its label")
        }
    }

    func testReportChipDescriptionAppendsDetailWhenPresent() {
        XCTAssertEqual(
            ReportChip.description(chip: .typo, proposed: nil, detail: "missing comma"),
            "Typo or wording: missing comma"
        )
        XCTAssertEqual(
            ReportChip.description(chip: .confusing, proposed: nil, detail: "vague phrasing"),
            "Confusing or ambiguous: vague phrasing"
        )
    }

    func testReportChipDescriptionProposedOnlyForWrongAnswer() {
        // wrongAnswer includes proposed
        XCTAssertEqual(
            ReportChip.description(chip: .wrongAnswer, proposed: "True", detail: ""),
            "Marked answer is wrong · you think it's: True"
        )
        // All other chips ignore proposed
        for chip in [ReportChip.confusing, .typo, .other] {
            let result = ReportChip.description(chip: chip, proposed: "True", detail: "")
            XCTAssertEqual(result, chip.label, "chip \(chip) must not include proposed answer")
        }
    }

    func testReportChipDescriptionAllFieldsCombined() {
        XCTAssertEqual(
            ReportChip.description(chip: .wrongAnswer, proposed: "Option B", detail: "see explanation"),
            "Marked answer is wrong · you think it's: Option B: see explanation"
        )
    }

    func testReportChipDescriptionOmitsWhitespaceOnlyDetail() {
        let result = ReportChip.description(chip: .typo, proposed: nil, detail: "   \n  ")
        XCTAssertEqual(result, "Typo or wording")
    }

    func testReportContextCarriesPromptAndOptionsForMC() {
        let mcQuestion = SeededStudyData.questions.first { $0.question.type == .multipleChoice }!
        let options: [String] = {
            if case .multipleChoice(let q) = mcQuestion.question { return q.options }
            return []
        }()
        let context = ReportQuestionContext(
            identity: mcQuestion.identity,
            qid: mcQuestion.qid,
            questionType: mcQuestion.question.type,
            appVersion: "1.0",
            build: "1",
            selectedResponse: nil,
            prompt: mcQuestion.prompt,
            options: options
        )
        XCTAssertEqual(context.prompt, mcQuestion.prompt)
        XCTAssertFalse(context.options.isEmpty, "MC question must supply options to the report context")
    }

    func testReportContextCarriesTrueFalseOptions() {
        let tfQuestion = SeededStudyData.questions.first { $0.question.type == .trueFalse }!
        let context = ReportQuestionContext(
            identity: tfQuestion.identity,
            qid: tfQuestion.qid,
            questionType: tfQuestion.question.type,
            appVersion: "1.0",
            build: "1",
            selectedResponse: nil,
            prompt: tfQuestion.prompt,
            options: ["True", "False"]
        )
        XCTAssertEqual(context.options, ["True", "False"])
        XCTAssertEqual(context.prompt, tfQuestion.prompt)
    }

    // MARK: - TodayRecommendation tests

    func testTodayRecommendationAllThreeCases() {
        // 1. Review when due > 0
        let review = TodayRecommendation(due: 6, unseen: 10, sessionLimit: 10)
        XCTAssertTrue(review.isReview)
        XCTAssertEqual(review.title, "Scheduled review: 6 questions")
        XCTAssertEqual(review.detail, "Spaced repetition · about 5 minutes")
        XCTAssertEqual(review.buttonTitle, "Start review")

        // 2. Learn when due == 0 and unseen > 0
        let learn = TodayRecommendation(due: 0, unseen: 34, sessionLimit: 10)
        XCTAssertTrue(learn.isLearn)
        XCTAssertEqual(learn.title, "Ready to learn")
        XCTAssertEqual(learn.detail, "Learn 10 new questions · about 8 minutes")
        XCTAssertEqual(learn.buttonTitle, "Start learning")

        // 3. Caught up when due == 0 and unseen == 0
        let caughtUp = TodayRecommendation(due: 0, unseen: 0, sessionLimit: 10)
        XCTAssertTrue(caughtUp.isCaughtUp)
        XCTAssertEqual(caughtUp.title, "Ready to practice")
        XCTAssertEqual(caughtUp.detail, "About 8 minutes")
        XCTAssertEqual(caughtUp.buttonTitle, "Keep practicing")
    }

    func testTodayRecommendationPluralisation() {
        // Single question due
        let singleDue = TodayRecommendation(due: 1, unseen: 10, sessionLimit: 10)
        XCTAssertEqual(singleDue.title, "Scheduled review: 1 question")
        XCTAssertEqual(singleDue.detail, "Spaced repetition · about 1 minute")

        // Multiple questions due
        let multiDue = TodayRecommendation(due: 6, unseen: 10, sessionLimit: 10)
        XCTAssertEqual(multiDue.title, "Scheduled review: 6 questions")
        XCTAssertEqual(multiDue.detail, "Spaced repetition · about 5 minutes")

        // Single new question to learn
        let singleLearn = TodayRecommendation(due: 0, unseen: 1, sessionLimit: 10)
        XCTAssertEqual(singleLearn.title, "Ready to learn")
        XCTAssertEqual(singleLearn.detail, "Learn 1 new question · about 1 minute")

        // Multiple new questions to learn
        let multiLearn = TodayRecommendation(due: 0, unseen: 34, sessionLimit: 6)
        XCTAssertEqual(multiLearn.title, "Ready to learn")
        XCTAssertEqual(multiLearn.detail, "Learn 6 new questions · about 5 minutes")

        // Batch smaller than limit prints unseen count
        let smallBatchLearn = TodayRecommendation(due: 0, unseen: 3, sessionLimit: 10)
        XCTAssertEqual(smallBatchLearn.detail, "Learn 3 new questions · about 3 minutes")

        // Single minute in caughtUp
        let singleMinuteCaughtUp = TodayRecommendation(due: 0, unseen: 0, sessionLimit: 1)
        XCTAssertEqual(singleMinuteCaughtUp.detail, "About 1 minute")

        // Multiple minutes in caughtUp
        let multiMinutesCaughtUp = TodayRecommendation(due: 0, unseen: 0, sessionLimit: 6)
        XCTAssertEqual(multiMinutesCaughtUp.detail, "About 5 minutes")
    }

    func testPausedScheduledReviewKeepsReviewLanguageOutOfTodayRecommendation() {
        let recommendation = TodayRecommendation(
            due: 6,
            unseen: 10,
            sessionLimit: 10,
            scheduledReviewEnabled: false
        )

        XCTAssertTrue(recommendation.isLearn)
        XCTAssertFalse(recommendation.title.localizedCaseInsensitiveContains("review"))
        XCTAssertFalse(recommendation.detail.localizedCaseInsensitiveContains("review"))
        XCTAssertFalse(recommendation.detail.localizedCaseInsensitiveContains("due"))
    }

    func testGlobalProgressStatusUsesCompactLiveLabels() {
        XCTAssertEqual(GlobalProgressStatusControl.compactLabel(for: .local), "Saved")
        XCTAssertEqual(GlobalProgressStatusControl.compactLabel(for: .synced), "Synced")
        XCTAssertEqual(GlobalProgressStatusControl.compactLabel(for: .syncPending), "Pending sync")
        XCTAssertEqual(GlobalProgressStatusControl.compactLabel(for: .saveFailed), "Retry save")
    }

    func testGlobalProgressStatusIconAndStyleMapping() {
        // Visible success label is "Synced"
        XCTAssertEqual(GlobalProgressStatusControl.compactLabel(for: .synced), "Synced")
        // Success state uses a green cloud
        XCTAssertEqual(GlobalProgressStatusControl.icon(for: .synced), "checkmark.icloud.fill")
        XCTAssertEqual(GlobalProgressStatusControl.iconColor(for: .synced), QuizzlerTheme.success)

        // Non-synced failure/pending states use a red cloud
        let nonSyncedTerminalStates: [LaunchpadProgressModel.PersistenceState] = [
            .syncPending,
            .accountChanged,
            .saveFailed
        ]
        for state in nonSyncedTerminalStates {
            XCTAssertEqual(GlobalProgressStatusControl.icon(for: state), "exclamationmark.icloud.fill")
            XCTAssertEqual(GlobalProgressStatusControl.iconColor(for: state), QuizzlerTheme.danger)
        }

        // In-progress/loading and local-only states remain neutral
        let neutralStates: [LaunchpadProgressModel.PersistenceState] = [
            .loading,
            .local,
            .saving,
            .syncing
        ]
        for state in neutralStates {
            XCTAssertEqual(GlobalProgressStatusControl.iconColor(for: state), QuizzlerTheme.textMuted)
        }

        // Text color remains high contrast
        XCTAssertEqual(GlobalProgressStatusControl.textColor, QuizzlerTheme.textPrimary)
    }

    func testPersistenceStatusReportsLastSyncSucceeded() {
        XCTAssertEqual(LaunchpadProgressModel.persistenceStatus(for: .synced), "last sync succeeded")
    }

    func testTappingSyncedBadgeExplicitlyRequestsFreshSynchronization() async throws {
        let repository = ControlledProgressRepository(syncMode: .cloudKit)
        let model = LaunchpadProgressModel(repository: repository)

        model.load()
        try await waitForProgressState(.synced, in: model)
        let initialCalls = await repository.synchronizeCallCount()
        XCTAssertEqual(initialCalls, 1)
        XCTAssertEqual(model.persistenceState, .synced)
        XCTAssertEqual(GlobalProgressStatusControl.compactLabel(for: model.persistenceState), "Synced")
        XCTAssertEqual(model.persistenceStatus, "last sync succeeded")

        // Tapping the synced status badge calls model.synchronizeOnForeground()
        model.synchronizeOnForeground()
        try await waitForProgressState(.synced, in: model)

        let updatedCalls = await repository.synchronizeCallCount()
        XCTAssertEqual(updatedCalls, 2)
    }

    func testTodayRecommendationMinutesRounding() {
        // max(1, ceil(batch * 0.75))
        // batch 0 -> max(1, 0) = 1
        XCTAssertEqual(TodayRecommendation(due: 0, unseen: 0, sessionLimit: 0).minutes, 1)
        // batch 1 -> ceil(0.75) = 1 -> max(1, 1) = 1
        XCTAssertEqual(TodayRecommendation(due: 1, unseen: 0, sessionLimit: 10).minutes, 1)
        // batch 2 -> ceil(1.50) = 2 -> 2
        XCTAssertEqual(TodayRecommendation(due: 2, unseen: 0, sessionLimit: 10).minutes, 2)
        // batch 3 -> ceil(2.25) = 3 -> 3
        XCTAssertEqual(TodayRecommendation(due: 3, unseen: 0, sessionLimit: 10).minutes, 3)
        // batch 4 -> ceil(3.00) = 3 -> 3
        XCTAssertEqual(TodayRecommendation(due: 4, unseen: 0, sessionLimit: 10).minutes, 3)
        // batch 5 -> ceil(3.75) = 4 -> 4
        XCTAssertEqual(TodayRecommendation(due: 5, unseen: 0, sessionLimit: 10).minutes, 4)
        // batch 6 -> ceil(4.50) = 5 -> 5
        XCTAssertEqual(TodayRecommendation(due: 6, unseen: 0, sessionLimit: 10).minutes, 5)
        // batch 10 -> ceil(7.50) = 8 -> 8
        XCTAssertEqual(TodayRecommendation(due: 10, unseen: 0, sessionLimit: 10).minutes, 8)
    }

    func testTodayRecommendationBatchCap() {
        // Due review capped at sessionLimit
        let cappedReview = TodayRecommendation(due: 25, unseen: 0, sessionLimit: 10)
        XCTAssertEqual(cappedReview.batch, 10)
        XCTAssertEqual(cappedReview.minutes, 8)
        XCTAssertEqual(cappedReview.title, "Scheduled review: 10 questions")
        XCTAssertEqual(cappedReview.detail, "Spaced repetition · 25 due overall · about 8 minutes")

        let uncappedReview = TodayRecommendation(due: 4, unseen: 0, sessionLimit: 10)
        XCTAssertEqual(uncappedReview.batch, 4)
        XCTAssertEqual(uncappedReview.minutes, 3)

        // Learn capped at sessionLimit
        let cappedLearn = TodayRecommendation(due: 0, unseen: 50, sessionLimit: 20)
        XCTAssertEqual(cappedLearn.batch, 20)
        XCTAssertEqual(cappedLearn.minutes, 15)
        XCTAssertEqual(cappedLearn.detail, "Learn 20 new questions · about 15 minutes")

        let uncappedLearn = TodayRecommendation(due: 0, unseen: 5, sessionLimit: 20)
        XCTAssertEqual(uncappedLearn.batch, 5)
        XCTAssertEqual(uncappedLearn.minutes, 4)

        // CaughtUp batch is sessionLimit
        let caughtUp = TodayRecommendation(due: 0, unseen: 0, sessionLimit: 12)
        XCTAssertEqual(caughtUp.batch, 12)
        XCTAssertEqual(caughtUp.minutes, 9)
    }

    func testTodayRecommendationHidesScheduledReviewWhenPaused() {
        let paused = TodayRecommendation(due: 25, unseen: 3, sessionLimit: 10, scheduledReviewEnabled: false)
        XCTAssertTrue(paused.isLearn)
        XCTAssertFalse(paused.isReview)
        XCTAssertEqual(paused.title, "Ready to learn")
        XCTAssertEqual(paused.detail, "Learn 3 new questions · about 3 minutes")

        let practice = TodayRecommendation(due: 25, unseen: 0, sessionLimit: 10, scheduledReviewEnabled: false)
        XCTAssertTrue(practice.isCaughtUp)
        XCTAssertEqual(practice.title, "Ready to practice")
    }

    // MARK: - seenIdentities tests

    func testLaunchpadProgressModelSeenIdentities() async throws {
        let fileURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("QuizzleriOSTests-seenIdentities-\(UUID().uuidString)", isDirectory: false)
        addTeardownBlock { try? FileManager.default.removeItem(at: fileURL) }

        let targetID1 = QuestionIdentity(courseID: "cysa", packID: "core", questionID: "q1")
        let targetID2 = QuestionIdentity(courseID: "cysa", packID: "core", questionID: "q2")
        let otherPackID = QuestionIdentity(courseID: "cysa", packID: "other", questionID: "q3")
        let otherCourseID = QuestionIdentity(courseID: "cissp", packID: "core", questionID: "q4")
        let zeroAnsweredID = QuestionIdentity(courseID: "cysa", packID: "core", questionID: "q-zero")

        let store = LocalProgressStore(fileURL: fileURL)
        let initialEnvelope = ProgressEnvelope(
            actorID: "test-device",
            mastery: [
                MasterySnapshot(identity: targetID1, answered: 2, correct: 1),
                MasterySnapshot(identity: otherPackID, answered: 1, correct: 1),
                MasterySnapshot(identity: otherCourseID, answered: 3, correct: 2),
                MasterySnapshot(identity: zeroAnsweredID, answered: 0, correct: 0)
            ]
        )
        try await store.write(initialEnvelope)

        let repository = ProgressRepository(actorID: "test-device", store: store)
        let model = LaunchpadProgressModel(repository: repository)
        model.load()
        for _ in 0..<100 {
            if model.persistenceState == .local { break }
            try await Task.sleep(nanoseconds: 5_000_000)
        }

        // Before adding pending answers: targetID1 is seen; zeroAnsweredID is not seen
        let initialSeen = model.seenIdentities(courseID: "cysa", packID: "core")
        XCTAssertEqual(initialSeen, Set([targetID1]))

        // Record a pending answer for targetID2 and duplicate answer for targetID1
        model.record(SessionAnswer(identity: targetID2, correct: true))
        model.record(SessionAnswer(identity: targetID1, correct: false))
        // And record a pending answer for another pack
        model.record(SessionAnswer(identity: otherPackID, correct: true))

        let updatedSeen = model.seenIdentities(courseID: "cysa", packID: "core")
        // Must contain both targetID1 and targetID2, but NOT otherPackID, otherCourseID, or zeroAnsweredID
        XCTAssertEqual(updatedSeen, Set([targetID1, targetID2]))
    }

}

@MainActor
private final class RegistrationRecorder {
    private(set) var count = 0

    func record() {
        count += 1
    }
}

private actor ControlledProgressRepository: LaunchpadProgressRepository {
    private var aggregate = AggregateSnapshot()
    private var schemaVersion: Int
    private var maximumLeitnerLevel = LeitnerSchedule.defaultMaximumLevel
    private var maximumLevelSetCalls = 0
    private var snapshotCalls = 0
    private var saveCalls = 0
    private var synchronizeCalls = 0
    private var failingSnapshotCalls: Set<Int>
    private var snapshotContinuation: AsyncStream<ProgressEnvelope>.Continuation?
    private var snapshotStreamReady = false
    private var streamReadyWaiters: [CheckedContinuation<Void, Never>] = []
    private var statusContinuation: AsyncStream<SyncStatusEvent>.Continuation?
    private var statusStreamReady = false
    private var statusStreamReadyWaiters: [CheckedContinuation<Void, Never>] = []
    nonisolated let syncMode: LaunchpadSyncMode

    init(
        failingSnapshotCalls: Set<Int> = [],
        syncMode: LaunchpadSyncMode = .local,
        schemaVersion: Int = ProgressEnvelope.currentSchemaVersion
    ) {
        self.failingSnapshotCalls = failingSnapshotCalls
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
}

private actor RuntimeCloudTransport: CloudProgressTransport {
    private var sends = 0
    private let sendError: CloudProgressTransportError?
    private let fetchRecords: [CloudKitMappedRecord]

    init(
        sendError: CloudProgressTransportError? = nil,
        fetchRecords: [CloudKitMappedRecord] = []
    ) {
        self.sendError = sendError
        self.fetchRecords = fetchRecords
    }

    func fetchChanges() async throws -> CloudProgressFetchResult { CloudProgressFetchResult() }

    func fetchChanges(full: Bool) async throws -> CloudProgressFetchResult {
        CloudProgressFetchResult(records: fetchRecords, isFullSnapshot: full)
    }

    func sendChanges(_ records: [CloudKitMappedRecord]) async throws -> CloudProgressSendResult {
        CloudProgressSendResult(savedRecordNames: records.map(\.recordName))
    }

    func sendProgressAtomically(
        _ records: [CloudKitMappedRecord],
        expectedRevision: Int,
        snapshotChangeTag: String?
    ) async throws -> CloudProgressSendResult {
        if let sendError { throw sendError }
        sends += 1
        let operations = try records
            .filter { $0.kind == .operation }
            .map { try CloudKitMapping.operation(from: $0) }
            .sorted { $0.id < $1.id }
        let assigned = Dictionary(uniqueKeysWithValues: operations.enumerated().map { index, operation in
            (operation.id, expectedRevision + index + 1)
        })
        return CloudProgressSendResult(
            savedRecordNames: records.map(\.recordName),
            snapshotChangeTag: "runtime-tag-\(sends)",
            assignedRevisions: assigned
        )
    }

    func sendIssuesAtomically(_ records: [CloudKitMappedRecord]) async throws -> CloudProgressSendResult {
        CloudProgressSendResult(savedRecordNames: records.map(\.recordName))
    }

    func deleteChanges(_ recordNames: [String]) async throws -> CloudProgressSendResult {
        CloudProgressSendResult(deletedRecordNames: recordNames)
    }

    func resetPendingChanges() async {}

    func progressSendCount() -> Int { sends }
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
