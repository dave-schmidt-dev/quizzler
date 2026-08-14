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
}
