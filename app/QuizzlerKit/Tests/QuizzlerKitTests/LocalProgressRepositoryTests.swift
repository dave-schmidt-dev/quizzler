import Foundation
import XCTest
@testable import QuizzlerKit

final class LocalProgressRepositoryTests: XCTestCase {
    private func session(_ number: Int, answer: Bool = true) -> SessionDetail {
        SessionDetail(sessionID: "session-\(number)", completedAt: Date(timeIntervalSince1970: Double(number)), answers: [
            SessionAnswer(courseID: "course", packID: "pack", questionID: "q-\(number)", correct: answer)
        ])
    }

    func testSavePersistsAggregateAndRetainsOnlyLatest200Details() async throws {
        let store = LocalProgressStore()
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
        let store = LocalProgressStore()
        let repository = ProgressRepository(actorID: "device-a", store: store)
        let intent = ProgressOperation.newIntent(session: session(1))
        _ = try await repository.enqueue(intent)
        let retry = try await repository.retry(operationID: intent.id)
        XCTAssertEqual(retry.id, intent.id)
        let retriedSnapshot = try await repository.snapshot()
        XCTAssertEqual(retriedSnapshot.operations.count, 1)
    }

    func testSaveReplayWithKnownOperationIDDoesNotApplyTwice() async throws {
        let store = LocalProgressStore()
        let repository = ProgressRepository(actorID: "device-a", store: store)
        let completed = session(1)

        _ = try await repository.save(completed, operationID: "operation-1")
        let replay = try await repository.save(completed, operationID: "operation-1")
        let snapshot = try await repository.snapshot()

        XCTAssertEqual(replay.id, "operation-1")
        XCTAssertEqual(snapshot.aggregate, AggregateSnapshot(sessionsTotal: 1, answered: 1, correct: 1))
        XCTAssertEqual(snapshot.sessionDetails, [completed])
        XCTAssertEqual(snapshot.mastery, [MasterySnapshot(identity: completed.answers[0].identity, answered: 1, correct: 1)])
        XCTAssertEqual(snapshot.operations.count, 1)
    }

    func testFailedAndSizeRefusedWritesDoNotBecomeDurable() async throws {
        let store = LocalProgressStore(maximumEncodedSize: 1_024_000)
        let repository = ProgressRepository(actorID: "device-a", store: store)
        _ = try await repository.save(session(1))
        await store.failNextWrite()
        do { _ = try await repository.save(session(2)); XCTFail("failed write must throw") } catch { }
        let failedSnapshot = try await repository.snapshot()
        XCTAssertEqual(failedSnapshot.aggregate.sessionsTotal, 1)

        let refusingStore = LocalProgressStore(maximumEncodedSize: 1)
        let refusingRepository = ProgressRepository(actorID: "device-a", store: refusingStore)
        do { _ = try await refusingRepository.save(session(1)); XCTFail("size refusal must throw") } catch { }
        let refusedSnapshot = try await refusingRepository.snapshot()
        XCTAssertEqual(refusedSnapshot.aggregate.sessionsTotal, 0)
    }
}
