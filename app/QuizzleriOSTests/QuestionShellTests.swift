import XCTest
import CryptoKit
import UIKit
@testable import QuizzleriOS
import QuizzlerKit

@MainActor
final class QuestionShellTests: XCTestCase {
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
        XCTAssertEqual(snapshotCalls, 2)
        XCTAssertEqual(model.aggregate, AggregateSnapshot())
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
            studyQuestion: SeededStudyData.questions[0],
            phase: .question,
            repository: repository,
            selection: .constant(.none),
            onCheck: { _ in },
            onFinish: {}
        )
        XCTAssertEqual(shell.repository.syncMode, .local)
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
        syncMode: LaunchpadSyncMode = .local
    ) {
        self.failingSnapshotCalls = failingSnapshotCalls
        self.syncMode = syncMode
    }

    func snapshot() async throws -> ProgressEnvelope {
        snapshotCalls += 1
        if failingSnapshotCalls.remove(snapshotCalls) != nil {
            throw ProgressRepositoryError.failed("test snapshot failure")
        }
        return ProgressEnvelope(actorID: "test-device", aggregate: aggregate)
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
        snapshotContinuation?.yield(ProgressEnvelope(actorID: "remote-device", aggregate: aggregate))
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
