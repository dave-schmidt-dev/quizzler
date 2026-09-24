import Foundation
import XCTest
@testable import QuizzlerKit

final class LocalProgressRepositoryTests: XCTestCase {
    private func temporaryFileURL() -> URL {
        let fileURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("QuizzlerKitTests-\(UUID().uuidString)", isDirectory: false)
        addTeardownBlock {
            try? FileManager.default.removeItem(at: fileURL)
        }
        return fileURL
    }

    private func session(_ number: Int, answer: Bool = true) -> SessionDetail {
        SessionDetail(sessionID: "session-\(number)", completedAt: Date(timeIntervalSince1970: Double(number)), answers: [
            SessionAnswer(courseID: "course", packID: "pack", questionID: "q-\(number)", correct: answer)
        ])
    }

    func testSavePersistsAggregateAndRetainsOnlyLatest200Details() async throws {
        let store = LocalProgressStore(fileURL: temporaryFileURL())
        let repository = ProgressRepository(actorID: "device-a", store: store)
        for number in 0..<201 { _ = try await repository.save(session(number)) }
        let snapshot = try await repository.snapshot()
        XCTAssertEqual(snapshot.aggregate.sessionsTotal, 201)
        XCTAssertEqual(snapshot.aggregate.answered, 201)
        XCTAssertEqual(snapshot.sessionDetails.count, 200)
        XCTAssertEqual(snapshot.sessionDetails.first?.sessionID, "session-1")
        XCTAssertEqual(snapshot.sessionDetails.last?.sessionID, "session-200")
        XCTAssertEqual(snapshot.mastery.count, 201)
    }

    func testOperationIDIsStableWhenPendingIntentIsRetried() async throws {
        let store = LocalProgressStore(fileURL: temporaryFileURL())
        let repository = ProgressRepository(actorID: "device-a", store: store)
        let intent = ProgressOperation.newIntent(session: session(1))
        _ = try await repository.enqueue(intent)
        let retry = try await repository.retry(operationID: intent.id)
        XCTAssertEqual(retry.id, intent.id)
        let retriedSnapshot = try await repository.snapshot()
        XCTAssertEqual(retriedSnapshot.operations.count, 1)
    }

    func testSaveReplayWithKnownOperationIDDoesNotApplyTwice() async throws {
        let store = LocalProgressStore(fileURL: temporaryFileURL())
        let repository = ProgressRepository(actorID: "device-a", store: store)
        let completed = session(1)

        let original = try await repository.save(completed)
        let replay = try await repository.save(completed, operationID: original.id)
        let snapshot = try await repository.snapshot()

        XCTAssertEqual(replay.id, original.id)
        XCTAssertEqual(snapshot.aggregate, AggregateSnapshot(sessionsTotal: 1, answered: 1, correct: 1))
        XCTAssertEqual(snapshot.sessionDetails, [completed])
        XCTAssertEqual(snapshot.mastery, [MasterySnapshot(identity: completed.answers[0].identity, answered: 1, correct: 1)])
        XCTAssertEqual(snapshot.operations.count, 1)
    }

    func testPrunedOperationIDReplayIsRefusedWithoutChangingProgress() async throws {
        let fileURL = temporaryFileURL()
        let original = session(1)
        let retained = (0..<ProgressEnvelope.operationRetention).map { index in
            ProgressOperation(operationID: "retained-\(index)", status: .applied)
        }
        let baseline = ProgressEnvelope(
            actorID: "device-a",
            sessionDetails: [original],
            aggregate: AggregateSnapshot(sessionsTotal: ProgressEnvelope.operationRetention + 1, answered: 1, correct: 1),
            mastery: [MasterySnapshot(identity: original.answers[0].identity, answered: 1, correct: 1)],
            operations: retained
        )
        let store = LocalProgressStore(fileURL: fileURL)
        try await store.write(baseline)
        let repository = ProgressRepository(actorID: "device-a", store: LocalProgressStore(fileURL: fileURL))

        do {
            _ = try await repository.save(original, operationID: "pruned-operation")
            XCTFail("an operation ID absent from the bounded ledger must not be applied again")
        } catch let error as ProgressRepositoryError {
            XCTAssertEqual(error, .operationNotFound)
        }

        let reloadedEnvelope = try await LocalProgressStore(fileURL: fileURL).read()
        let reloaded = try XCTUnwrap(reloadedEnvelope)
        XCTAssertEqual(reloaded.documentRevision, baseline.documentRevision)
        XCTAssertEqual(reloaded.aggregate, baseline.aggregate)
        XCTAssertEqual(reloaded.sessionDetails, baseline.sessionDetails)
        XCTAssertEqual(reloaded.mastery, baseline.mastery)
        XCTAssertEqual(reloaded.operations.map(\.id), retained.map(\.id))
    }

    func testFailedAndSizeRefusedWritesDoNotBecomeDurable() async throws {
#if DEBUG
        let store = LocalProgressStore(fileURL: temporaryFileURL(), maximumEncodedSize: 1_024_000)
        let repository = ProgressRepository(actorID: "device-a", store: store)
        _ = try await repository.save(session(1))
        await store.failNextWrite()
        do { _ = try await repository.save(session(2)); XCTFail("failed write must throw") } catch { }
        let failedSnapshot = try await repository.snapshot()
        XCTAssertEqual(failedSnapshot.aggregate.sessionsTotal, 1)

        let refusingStore = LocalProgressStore(fileURL: temporaryFileURL(), maximumEncodedSize: 1)
        let refusingRepository = ProgressRepository(actorID: "device-a", store: refusingStore)
        do { _ = try await refusingRepository.save(session(1)); XCTFail("size refusal must throw") } catch { }
        let refusedSnapshot = try await refusingRepository.snapshot()
        XCTAssertEqual(refusedSnapshot.aggregate.sessionsTotal, 0)
#else
        throw XCTSkip("write fault injection is debug-only")
#endif
    }

    func testEnvelopeReloadsUnchangedAcrossStoreInstances() async throws {
        let fileURL = temporaryFileURL()
        let repositoryA = ProgressRepository(
            actorID: "device-a",
            store: LocalProgressStore(fileURL: fileURL)
        )
        _ = try await repositoryA.save(session(1))
        let persisted = try await LocalProgressStore(fileURL: fileURL).read()
        guard let persisted else {
            XCTFail("save must create a durable envelope")
            return
        }

        let repositoryB = ProgressRepository(
            actorID: "device-a",
            store: LocalProgressStore(fileURL: fileURL)
        )
        let reloaded = try await repositoryB.snapshot()

        XCTAssertEqual(reloaded, persisted)
    }

    func testFailedPendingOperationIsDurableAndRetryPreservesProgress() async throws {
        let fileURL = temporaryFileURL()
        let repository = ProgressRepository(
            actorID: "device-a",
            store: LocalProgressStore(fileURL: fileURL)
        )
        let intent = ProgressOperation.newIntent(session: session(1))
        _ = try await repository.enqueue(intent)

        let before = try await repository.snapshot()
        let failed = try await repository.markFailed(operationID: intent.id, error: .failed("offline"))
        XCTAssertEqual(failed.id, intent.id)
        XCTAssertEqual(failed.session, intent.session)
        XCTAssertEqual(failed.status, .failed)
        XCTAssertEqual(failed.error, .failed("offline"))

        let reloadedRepository = ProgressRepository(
            actorID: "device-a",
            store: LocalProgressStore(fileURL: fileURL)
        )
        let failedSnapshot = try await reloadedRepository.snapshot()
        XCTAssertEqual(failedSnapshot.aggregate, before.aggregate)
        XCTAssertEqual(failedSnapshot.sessionDetails, before.sessionDetails)
        XCTAssertEqual(failedSnapshot.mastery, before.mastery)
        XCTAssertEqual(failedSnapshot.srs, before.srs)
        XCTAssertEqual(failedSnapshot.operations.first?.id, intent.id)
        XCTAssertEqual(failedSnapshot.operations.first?.status, .failed)
        XCTAssertEqual(failedSnapshot.operations.first?.session, intent.session)
        XCTAssertEqual(failedSnapshot.operations.first?.error, .failed("offline"))

        let retried = try await reloadedRepository.retry(operationID: intent.id)
        XCTAssertEqual(retried.id, intent.id)
        XCTAssertEqual(retried.session, intent.session)
        XCTAssertEqual(retried.status, .pending)
        XCTAssertNil(retried.error)

        let retriedSnapshot = try await reloadedRepository.snapshot()
        XCTAssertEqual(retriedSnapshot.aggregate, before.aggregate)
        XCTAssertEqual(retriedSnapshot.sessionDetails, before.sessionDetails)
        XCTAssertEqual(retriedSnapshot.mastery, before.mastery)
        XCTAssertEqual(retriedSnapshot.srs, before.srs)
        XCTAssertEqual(retriedSnapshot.operations.count, 1)
        XCTAssertEqual(retriedSnapshot.operations.first?.id, intent.id)
        XCTAssertEqual(retriedSnapshot.operations.first?.session, intent.session)
        XCTAssertEqual(retriedSnapshot.operations.first?.status, .pending)
        XCTAssertNil(retriedSnapshot.operations.first?.error)
    }

    func testMarkFailedOnlyAcceptsPendingOperations() async throws {
        let store = LocalProgressStore(fileURL: temporaryFileURL())
        let repository = ProgressRepository(actorID: "device-a", store: store)
        let intent = ProgressOperation.newIntent(session: session(1))
        _ = try await repository.enqueue(intent)
        _ = try await repository.markFailed(operationID: intent.id, error: .failed("first"))

        do {
            _ = try await repository.markFailed(operationID: intent.id, error: .failed("second"))
            XCTFail("a failed operation must not be marked failed again")
        } catch let error as ProgressRepositoryError {
            XCTAssertEqual(error, .invalidOperation)
        }
    }

    func testAnswersUpdateOneDeterministicSRSnapshotPerIdentity() async throws {
        let fileURL = temporaryFileURL()
        let repository = ProgressRepository(
            actorID: "device-a",
            store: LocalProgressStore(fileURL: fileURL)
        )
        let identity = QuestionIdentity(courseID: "course", packID: "pack", questionID: "repeat")
        let firstDate = Date(timeIntervalSince1970: 1_000_000)
        let first = SessionDetail(
            sessionID: "srs-1",
            completedAt: firstDate,
            answers: [SessionAnswer(identity: identity, correct: true)]
        )
        _ = try await repository.save(first, now: firstDate)

        var snapshot = try await repository.snapshot()
        XCTAssertEqual(snapshot.srs.count, 1)
        XCTAssertEqual(snapshot.srs[0].identity, identity)
        XCTAssertEqual(snapshot.srs[0].state.tier, 2)
        XCTAssertEqual(snapshot.srs[0].state.intervalDays, 3)
        XCTAssertEqual(snapshot.srs[0].state.reviewCount, 1)
        XCTAssertEqual(snapshot.srs[0].state.lastReviewedAt, firstDate)
        XCTAssertEqual(snapshot.srs[0].state.nextDueAt, firstDate.addingTimeInterval(3 * 86_400))

        let secondDate = firstDate.addingTimeInterval(86_400)
        let second = SessionDetail(
            sessionID: "srs-2",
            completedAt: secondDate,
            answers: [SessionAnswer(identity: identity, correct: false)]
        )
        _ = try await repository.save(second, now: secondDate)
        snapshot = try await repository.snapshot()
        XCTAssertEqual(snapshot.srs.count, 1)
        XCTAssertEqual(snapshot.srs[0].state.tier, 1)
        XCTAssertEqual(snapshot.srs[0].state.intervalDays, 1)
        XCTAssertEqual(snapshot.srs[0].state.reviewCount, 2)
        XCTAssertEqual(snapshot.srs[0].state.lastReviewedAt, secondDate)
        XCTAssertEqual(snapshot.srs[0].state.nextDueAt, secondDate.addingTimeInterval(86_400))
    }

    func testCapturedAnswerTimeDrivesSRSAndLegacyAnswerFallsBackToSessionCompletion() async throws {
        let repository = ProgressRepository(actorID: "device-a", store: LocalProgressStore(fileURL: temporaryFileURL()))
        let identity = QuestionIdentity(courseID: "course", packID: "pack", questionID: "captured-time")
        let completedAt = Date(timeIntervalSince1970: 1_000)
        let answeredAt = completedAt.addingTimeInterval(-120)
        let captured = SessionDetail(
            sessionID: "captured",
            completedAt: completedAt,
            answers: [SessionAnswer(identity: identity, correct: true, answeredAt: answeredAt)]
        )
        _ = try await repository.save(captured, now: completedAt.addingTimeInterval(999))

        var snapshot = try await repository.snapshot()
        XCTAssertEqual(snapshot.srs.first?.state.lastReviewedAt, answeredAt)
        XCTAssertEqual(snapshot.srs.first?.state.nextDueAt, answeredAt.addingTimeInterval(3 * 86_400))

        let legacyCompletedAt = completedAt.addingTimeInterval(10_000)
        let legacy = SessionDetail(
            sessionID: "legacy",
            completedAt: legacyCompletedAt,
            answers: [SessionAnswer(identity: identity, correct: false)]
        )
        _ = try await repository.save(legacy, now: legacyCompletedAt.addingTimeInterval(999))
        snapshot = try await repository.snapshot()
        XCTAssertEqual(snapshot.srs.first?.state.lastReviewedAt, legacyCompletedAt)
        XCTAssertEqual(snapshot.srs.first?.state.nextDueAt, legacyCompletedAt.addingTimeInterval(86_400))
    }

    func testSessionAnswerTimestampDecodesLegacyPayloadAndOmitsNilEncoding() throws {
        let legacyJSON = """
        {"course_id":"course","pack_id":"pack","question_id":"question","correct":true}
        """.data(using: .utf8)!
        let legacy = try JSONDecoder().decode(SessionAnswer.self, from: legacyJSON)
        XCTAssertNil(legacy.answeredAt)

        let encoded = try JSONEncoder().encode(legacy)
        let encodedObject = try XCTUnwrap(JSONSerialization.jsonObject(with: encoded) as? [String: Any])
        XCTAssertNil(encodedObject["answered_at"])

        let timestamp = Date(timeIntervalSince1970: 42)
        let captured = SessionAnswer(identity: legacy.identity, correct: true, answeredAt: timestamp)
        let decoded = try JSONDecoder().decode(SessionAnswer.self, from: JSONEncoder().encode(captured))
        XCTAssertEqual(decoded.answeredAt, timestamp)
    }

    func testReviewEventReducerCapturesFirstPromotionMissAndCapAdjustment() throws {
        let identity = QuestionIdentity(courseID: "course", packID: "pack", questionID: "event")
        let firstAt = Date(timeIntervalSince1970: 1_000)
        var envelope = ProgressEnvelope(actorID: "device-a")
        let first = ProgressOperation(
            operationID: "first",
            createdAt: firstAt,
            status: .applied,
            session: SessionDetail(
                sessionID: "first-session",
                completedAt: firstAt.addingTimeInterval(10),
                answers: [SessionAnswer(identity: identity, correct: true, answeredAt: firstAt)]
            )
        )
        let firstEvents = envelope.applying(first)
        XCTAssertEqual(firstEvents.count, 1)
        XCTAssertEqual(firstEvents[0].id, "first:0")
        XCTAssertEqual(firstEvents[0].outcome, .correct)
        XCTAssertEqual(firstEvents[0].priorLevel, 1)
        XCTAssertEqual(firstEvents[0].resultingLevel, 2)
        XCTAssertEqual(firstEvents[0].eventTime, firstAt)
        XCTAssertEqual(firstEvents[0].resultingDueAt, firstAt.addingTimeInterval(3 * 86_400))

        let promotionAt = firstAt.addingTimeInterval(100)
        let promotion = ProgressOperation(
            operationID: "promotion",
            createdAt: promotionAt,
            status: .applied,
            session: SessionDetail(
                sessionID: "promotion-session",
                completedAt: promotionAt,
                answers: [SessionAnswer(identity: identity, correct: true, answeredAt: promotionAt)]
            )
        )
        let promotionEvents = envelope.applying(promotion)
        XCTAssertEqual(promotionEvents[0].priorLevel, 2)
        XCTAssertEqual(promotionEvents[0].resultingLevel, 3)

        let missAt = promotionAt.addingTimeInterval(100)
        let miss = ProgressOperation(
            operationID: "miss",
            createdAt: missAt,
            status: .applied,
            session: SessionDetail(
                sessionID: "miss-session",
                completedAt: missAt,
                answers: [SessionAnswer(identity: identity, correct: false, answeredAt: missAt)]
            )
        )
        let missEvents = envelope.applying(miss)
        XCTAssertEqual(missEvents[0].outcome, .missed)
        XCTAssertEqual(missEvents[0].priorLevel, 3)
        XCTAssertEqual(missEvents[0].resultingLevel, 1)

        let recovery = ProgressOperation(
            operationID: "recovery",
            createdAt: missAt.addingTimeInterval(50),
            status: .applied,
            session: SessionDetail(
                sessionID: "recovery-session",
                completedAt: missAt.addingTimeInterval(50),
                answers: [SessionAnswer(identity: identity, correct: true, answeredAt: missAt.addingTimeInterval(50))]
            )
        )
        _ = envelope.applying(recovery)
        let capAt = missAt.addingTimeInterval(100)
        var cap = ProgressOperation(
            operationID: "cap",
            createdAt: capAt,
            status: .applied,
            kind: .setMaximumLeitnerLevel,
            maximumLeitnerLevel: 1
        )
        cap.updatedAt = capAt
        let capEvents = envelope.applying(cap)
        XCTAssertEqual(capEvents.map(\.outcome), [.maximumLevelChanged])
        XCTAssertEqual(capEvents[0].priorLevel, 2)
        XCTAssertEqual(capEvents[0].resultingLevel, 1)
        XCTAssertEqual(capEvents[0].eventTime, capAt)
    }

    func testConcurrentSavesDoNotLoseReadModifyWriteUpdates() async throws {
        let repository = ProgressRepository(
            actorID: "device-a",
            store: LocalProgressStore(fileURL: temporaryFileURL())
        )

        try await withThrowingTaskGroup(of: ProgressOperation.self) { group in
            for number in 0..<50 {
                let currentSession = session(number)
                group.addTask { try await repository.save(currentSession) }
            }
            for try await _ in group { }
        }

        let snapshot = try await repository.snapshot()
        XCTAssertEqual(snapshot.aggregate.sessionsTotal, 50)
        XCTAssertEqual(snapshot.sessionDetails.count, 50)
        XCTAssertEqual(snapshot.operations.filter { $0.status == .applied }.count, 50)
    }

    func testQueueIssueIsIdempotentAndRejectsChangedPayloadForSameID() async throws {
        let repository = ProgressRepository(
            actorID: "device-a",
            store: LocalProgressStore(fileURL: temporaryFileURL())
        )
        let issue = try QuestionIssue(
            issueID: "issue-fixed",
            courseID: "course",
            packID: "pack",
            questionID: "q-1",
            questionType: .multipleChoice,
            appVersion: "1.0.0",
            build: "100",
            description: "Typo"
        )

        _ = try await repository.queueIssue(issue)
        _ = try await repository.queueIssue(issue)
        let snapshot = try await repository.snapshot()
        XCTAssertEqual(snapshot.issues, [issue])

        let changed = try QuestionIssue(
            issueID: issue.issueID,
            courseID: issue.courseID,
            packID: issue.packID,
            questionID: issue.questionID,
            questionType: issue.questionType,
            appVersion: issue.appVersion,
            build: issue.build,
            description: "Different payload"
        )
        do {
            _ = try await repository.queueIssue(changed)
            XCTFail("a changed payload must not reuse an issue ID")
        } catch let error as ProgressRepositoryError {
            XCTAssertEqual(error, .invalidOperation)
        }
    }

    func testPersistedInvalidSRSStateIsRejectedInsteadOfBypassingInvariant() async throws {
        let fileURL = temporaryFileURL()
        let identity = QuestionIdentity(courseID: "course", packID: "pack", questionID: "q-1")
        let state = try SRSState(tier: 1, nextDueAt: Date(timeIntervalSince1970: 1_000))
        let envelope = ProgressEnvelope(
            actorID: "device-a",
            srs: [SRSSnapshot(identity: identity, state: state)]
        )
        let store = LocalProgressStore(fileURL: fileURL)
        try await store.write(envelope)
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: fileURL)) as? [String: Any])
        var srs = try XCTUnwrap(object["srs"] as? [[String: Any]])
        var invalidState = try XCTUnwrap(srs[0]["state"] as? [String: Any])
        invalidState["tier"] = 99
        srs[0]["state"] = invalidState
        object["srs"] = srs
        try JSONSerialization.data(withJSONObject: object).write(to: fileURL)

        do {
            _ = try await LocalProgressStore(fileURL: fileURL).read()
            XCTFail("invalid SRS state must be rejected")
        } catch let error as LocalProgressStoreError {
            XCTAssertEqual(error, .corruptState)
        }
    }

    func testPersistedEmptySessionIDIsRejectedInsteadOfBypassingInvariant() async throws {
        let fileURL = temporaryFileURL()
        let envelope = ProgressEnvelope(
            actorID: "device-a",
            sessionDetails: [session(1)],
            aggregate: AggregateSnapshot(sessionsTotal: 1, answered: 1, correct: 1)
        )
        let store = LocalProgressStore(fileURL: fileURL)
        try await store.write(envelope)
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: fileURL)) as? [String: Any])
        var sessions = try XCTUnwrap(object["sessionDetails"] as? [[String: Any]])
        sessions[0]["id"] = ""
        object["sessionDetails"] = sessions
        try JSONSerialization.data(withJSONObject: object).write(to: fileURL)

        do {
            _ = try await LocalProgressStore(fileURL: fileURL).read()
            XCTFail("empty session ID must be rejected")
        } catch let error as LocalProgressStoreError {
            XCTAssertEqual(error, .corruptState)
        }
    }

    func testPersistedNegativeEnvelopeRevisionIsRejectedInsteadOfBypassingInvariant() async throws {
        let fileURL = temporaryFileURL()
        let store = LocalProgressStore(fileURL: fileURL)
        try await store.write(ProgressEnvelope(actorID: "device-a"))
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: fileURL)) as? [String: Any])
        object["documentRevision"] = -1
        try JSONSerialization.data(withJSONObject: object).write(to: fileURL)

        do {
            _ = try await LocalProgressStore(fileURL: fileURL).read()
            XCTFail("negative document revision must be rejected")
        } catch let error as LocalProgressStoreError {
            XCTAssertEqual(error, .corruptState)
        }
    }

    func testReadFailureIsTerminalAndCannotOverwriteCorruptStore() async throws {
        let fileURL = temporaryFileURL()
        let corruptData = Data("{\"broken\":true}".utf8)
        try corruptData.write(to: fileURL)
        let store = LocalProgressStore(fileURL: fileURL)

        for _ in 0..<2 {
            do {
                _ = try await store.read()
                XCTFail("corrupt state must fail closed")
            } catch let error as LocalProgressStoreError {
                XCTAssertEqual(error, .corruptState)
            }
        }
        do {
            try await store.write(ProgressEnvelope(actorID: "device-a"))
            XCTFail("a failed read must prevent overwrite")
        } catch let error as LocalProgressStoreError {
            XCTAssertEqual(error, .corruptState)
        }
        XCTAssertEqual(try Data(contentsOf: fileURL), corruptData)
    }

    func testUnreadableStoreCanRecoverAndBecomeWritable() async throws {
        let fileURL = temporaryFileURL()
        try FileManager.default.createDirectory(at: fileURL, withIntermediateDirectories: true)
        let store = LocalProgressStore(fileURL: fileURL)

        for _ in 0..<2 {
            do {
                _ = try await store.read()
                XCTFail("unreadable state must fail closed")
            } catch let error as LocalProgressStoreError {
                XCTAssertEqual(error, .unavailable)
            }
        }
        try FileManager.default.removeItem(at: fileURL)
        let recovered = ProgressEnvelope(actorID: "device-a")
        try await store.write(recovered)
        let reloaded = try await store.read()
        XCTAssertEqual(reloaded, recovered)
    }

    func testOperationRetentionPreservesRetryableIntentsAndRefusesTooMany() async throws {
        let retainedURL = temporaryFileURL()
        let applied = (0..<4_095).map {
            ProgressOperation(operationID: "applied-\($0)", status: .applied)
        }
        let pending = [
            ProgressOperation(operationID: "pending-1", status: .pending),
            ProgressOperation(operationID: "pending-2", status: .failed, error: .failed("offline"))
        ]
        let retainedStore = LocalProgressStore(fileURL: retainedURL)
        try await retainedStore.write(ProgressEnvelope(actorID: "device-a", operations: applied + pending))
        let retained = try await LocalProgressStore(fileURL: retainedURL).read()
        XCTAssertEqual(retained?.operations.count, ProgressEnvelope.operationRetention)
        XCTAssertEqual(Set(retained?.operations.filter { $0.status != .applied }.map(\.id) ?? []), Set(["pending-1", "pending-2"]))

        let refusedURL = temporaryFileURL()
        let tooManyRetryable = (0...ProgressEnvelope.operationRetention).map {
            ProgressOperation(operationID: "pending-\($0)", status: .pending)
        }
        let refusedStore = LocalProgressStore(fileURL: refusedURL)
        do {
            try await refusedStore.write(ProgressEnvelope(actorID: "device-a", operations: tooManyRetryable))
            XCTFail("too many retryable operations must refuse persistence")
        } catch let error as LocalProgressStoreError {
            XCTAssertEqual(error, .encodedSizeRefused)
        }
        XCTAssertFalse(FileManager.default.fileExists(atPath: refusedURL.path))
    }

    func testIssueRetryCanReuseIDWithCurrentFieldsAfterFailedWrite() async throws {
#if DEBUG
        let store = LocalProgressStore(fileURL: temporaryFileURL())
        let repository = ProgressRepository(actorID: "device-a", store: store)
        let first = try QuestionIssue(issueID: "issue-retry", courseID: "course", packID: "pack", questionID: "q-1", questionType: .multipleChoice, appVersion: "1.0.0", build: "100", description: "first")
        let second = try QuestionIssue(issueID: first.issueID, courseID: first.courseID, packID: first.packID, questionID: first.questionID, questionType: first.questionType, appVersion: first.appVersion, build: first.build, description: "edited")

        await store.failNextWrite()
        do {
            _ = try await repository.queueIssue(first)
            XCTFail("faulted write must fail")
        } catch { }
        await store.clearWriteFailure()
        _ = try await repository.queueIssue(second)
        let snapshot = try await repository.snapshot()
        XCTAssertEqual(snapshot.issues, [second])
#else
        throw XCTSkip("write fault injection is debug-only")
#endif
    }

    func testDataProtectionWritePolicyIsPlatformBound() async throws {
        let fileURL = temporaryFileURL()
        let store = LocalProgressStore(fileURL: fileURL)
        try await store.write(ProgressEnvelope(actorID: "device-a"))
        let attributes = try FileManager.default.attributesOfItem(atPath: fileURL.path)

        #if os(iOS) || os(tvOS) || os(watchOS)
        #if targetEnvironment(simulator)
        throw XCTSkip("File protection attributes are not reported reliably by Apple platform simulators")
        #else
        XCTAssertEqual(attributes[.protectionKey] as? FileProtectionType, .complete)
        #endif
        #else
        XCTAssertNotEqual(attributes[.protectionKey] as? FileProtectionType, .complete)
        #endif
    }

    func testV1EnvelopeDecodesWithDefaultMaximumLeitnerLevel() throws {
        let v1 = ProgressEnvelope(schemaVersion: 1, actorID: "device-a")
        let encoded = try JSONEncoder().encode(v1)
        var object = try XCTUnwrap(try JSONSerialization.jsonObject(with: encoded) as? [String: Any])
        object.removeValue(forKey: "maximumLeitnerLevel")
        let legacyData = try JSONSerialization.data(withJSONObject: object)

        let decoded = try JSONDecoder().decode(ProgressEnvelope.self, from: legacyData)

        XCTAssertEqual(decoded.schemaVersion, 1)
        XCTAssertEqual(decoded.maximumLeitnerLevel, 5)
    }

    func testLegacyStatusOnlyOperationWithoutKindStillDecodes() throws {
        let legacy = ProgressOperation(operationID: "pending-legacy", status: .pending)
        let encoded = try JSONEncoder().encode(legacy)
        var object = try XCTUnwrap(try JSONSerialization.jsonObject(with: encoded) as? [String: Any])
        object.removeValue(forKey: "kind")
        let decoded = try JSONDecoder().decode(ProgressOperation.self, from: JSONSerialization.data(withJSONObject: object))
        XCTAssertEqual(decoded.kind, .review)
        XCTAssertNil(decoded.session)
        XCTAssertEqual(decoded.id, legacy.id)
    }

    func testV1HighTierIsPreservedUntilExplicitCapOperationMigratesIt() async throws {
        let reviewedAt = Date(timeIntervalSince1970: 10_000)
        let dueAt = reviewedAt.addingTimeInterval(120 * 86_400)
        let identity = QuestionIdentity(courseID: "course", packID: "pack", questionID: "legacy-high-tier")
        let state = try SRSState(
            tier: 7, nextDueAt: dueAt, lastReviewedAt: reviewedAt,
            intervalDays: 120, reviewCount: 7
        )
        let store = LocalProgressStore(fileURL: temporaryFileURL())
        try await store.write(ProgressEnvelope(
            schemaVersion: 1, actorID: "device-a",
            srs: [SRSSnapshot(identity: identity, state: state)]
        ))
        let repository = ProgressRepository(actorID: "device-a", store: store)
        let before = try await repository.snapshot()
        XCTAssertEqual(before.schemaVersion, 1)
        XCTAssertEqual(before.srs[0].state.tier, 7)
        XCTAssertEqual(before.srs[0].state.nextDueAt, dueAt)

        let operation = try await repository.setMaximumLeitnerLevel(5, now: reviewedAt.addingTimeInterval(1))
        let after = try await repository.snapshot()
        XCTAssertEqual(operation.kind, .setMaximumLeitnerLevel)
        XCTAssertEqual(after.schemaVersion, 2)
        XCTAssertEqual(after.maximumLeitnerLevel, 5)
        XCTAssertEqual(after.srs[0].state.tier, 5)
        XCTAssertEqual(after.srs[0].state.nextDueAt, reviewedAt.addingTimeInterval(30 * 86_400))
        XCTAssertEqual(after.srs[0].state.reviewCount, 7)
    }

    func testNewReviewUpgradesV1EnvelopeAndVersionTwoRequiresCapField() async throws {
        let store = LocalProgressStore(fileURL: temporaryFileURL())
        try await store.write(ProgressEnvelope(schemaVersion: 1, actorID: "device-a"))
        let repository = ProgressRepository(actorID: "device-a", store: store)
        _ = try await repository.save(session(1))
        let upgraded = try await repository.snapshot()
        XCTAssertEqual(upgraded.schemaVersion, 2)
        XCTAssertEqual(upgraded.maximumLeitnerLevel, 5)

        let encoded = try JSONEncoder().encode(upgraded)
        var object = try XCTUnwrap(try JSONSerialization.jsonObject(with: encoded) as? [String: Any])
        object.removeValue(forKey: "maximumLeitnerLevel")
        XCTAssertThrowsError(try JSONDecoder().decode(
            ProgressEnvelope.self, from: JSONSerialization.data(withJSONObject: object)
        ))
        object["schemaVersion"] = 3
        XCTAssertThrowsError(try JSONDecoder().decode(
            ProgressEnvelope.self, from: JSONSerialization.data(withJSONObject: object)
        ))
    }

    func testAnswersRespectConfiguredCapAndMissesStepDownByTwo() async throws {
        let repository = ProgressRepository(actorID: "device-a", store: LocalProgressStore(fileURL: temporaryFileURL()))
        let identity = QuestionIdentity(courseID: "course", packID: "pack", questionID: "repeat-cap")
        _ = try await repository.setMaximumLeitnerLevel(3, now: Date(timeIntervalSince1970: 100))

        for number in 1...4 {
            let at = Date(timeIntervalSince1970: Double(number) * 1_000)
            _ = try await repository.save(SessionDetail(
                sessionID: "cap-\(number)",
                completedAt: at,
                answers: [SessionAnswer(identity: identity, correct: true)]
            ), now: at)
        }
        var snapshot = try await repository.snapshot()
        XCTAssertEqual(snapshot.maximumLeitnerLevel, 3)
        XCTAssertEqual(snapshot.srs.first?.state.tier, 3)
        XCTAssertEqual(snapshot.srs.first?.state.intervalDays, 7)

        let missedAt = Date(timeIntervalSince1970: 5_000)
        _ = try await repository.save(SessionDetail(
            sessionID: "cap-miss",
            completedAt: missedAt,
            answers: [SessionAnswer(identity: identity, correct: false)]
        ), now: missedAt)
        snapshot = try await repository.snapshot()
        XCTAssertEqual(snapshot.srs.first?.state.tier, 1)
        XCTAssertEqual(snapshot.srs.first?.state.intervalDays, 1)
    }

    func testLoweringCapClampsTierAndNeverDelaysExistingDueDate() async throws {
        let repository = ProgressRepository(actorID: "device-a", store: LocalProgressStore(fileURL: temporaryFileURL()))
        let identity = QuestionIdentity(courseID: "course", packID: "pack", questionID: "lower-cap")
        let reviewedAt = Date(timeIntervalSince1970: 10_000)
        for number in 1...6 {
            _ = try await repository.save(SessionDetail(
                sessionID: "lower-\(number)",
                completedAt: reviewedAt,
                answers: [SessionAnswer(identity: identity, correct: true)]
            ), now: reviewedAt)
        }
        let beforeSnapshot = try await repository.snapshot()
        let before = try XCTUnwrap(beforeSnapshot.srs.first?.state)
        _ = try await repository.setMaximumLeitnerLevel(3, now: reviewedAt.addingTimeInterval(1))
        let afterSnapshot = try await repository.snapshot()
        let after = try XCTUnwrap(afterSnapshot.srs.first?.state)

        XCTAssertEqual(after.tier, 3)
        XCTAssertEqual(after.intervalDays, 7)
        XCTAssertEqual(after.reviewCount, before.reviewCount)
        XCTAssertEqual(after.lastReviewedAt, before.lastReviewedAt)
        XCTAssertEqual(after.nextDueAt, min(before.nextDueAt, reviewedAt.addingTimeInterval(7 * 86_400)))
    }

    func testUnchangedMaximumProducesNoReviewEvent() throws {
        let operation = ProgressOperation(
            operationID: "same-cap",
            createdAt: Date(timeIntervalSince1970: 1),
            status: .applied,
            kind: .setMaximumLeitnerLevel,
            maximumLeitnerLevel: 5
        )
        XCTAssertTrue(ProgressEnvelope.reviewEvents(
            for: operation,
            from: ProgressEnvelope(actorID: "device-a")
        ).isEmpty)
    }

    func testAppliedMaximumLevelOperationIDIsIdempotent() async throws {
        let repository = ProgressRepository(actorID: "device-a", store: LocalProgressStore(fileURL: temporaryFileURL()))
        let first = try await repository.setMaximumLeitnerLevel(3)
        let before = try await repository.snapshot()

        let retry = try await repository.setMaximumLeitnerLevel(3, operationID: first.id)
        let after = try await repository.snapshot()

        XCTAssertEqual(retry, first)
        XCTAssertEqual(after, before)
    }

    func testInvalidMaximumOperationLeavesEnvelopeUnchanged() throws {
        let identity = QuestionIdentity(courseID: "course", packID: "pack", questionID: "invalid-cap")
        let state = try SRSState(tier: 7, nextDueAt: Date(timeIntervalSince1970: 1))
        var envelope = ProgressEnvelope(
            actorID: "device-a",
            srs: [SRSSnapshot(identity: identity, state: state)]
        )
        let before = envelope
        let invalid = ProgressOperation(
            operationID: "invalid-cap",
            status: .applied,
            kind: .setMaximumLeitnerLevel,
            maximumLeitnerLevel: 0
        )

        XCTAssertTrue(envelope.applying(invalid).isEmpty)
        XCTAssertEqual(envelope, before)
    }
}
