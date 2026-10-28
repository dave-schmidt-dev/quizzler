import Foundation
import XCTest
@testable import QuizzlerKit

/// The decoder rejects an envelope above `ProgressEnvelope.operationRetention`
/// operations, so a local save must never persist more than that (INV-1):
/// otherwise the next launch cannot load the checkpoint.
final class CloudProgressOperationCapTests: XCTestCase {
    private final class IdleTransport: @unchecked Sendable, CloudProgressTransport {
        func fetchChanges() async throws -> CloudProgressFetchResult { CloudProgressFetchResult() }
        func sendChanges(_ records: [CloudKitMappedRecord]) async throws -> CloudProgressSendResult { CloudProgressSendResult() }
        func sendProgressAtomically(
            _ records: [CloudKitMappedRecord], expectedRevision: Int, snapshotChangeTag: String?
        ) async throws -> CloudProgressSendResult { CloudProgressSendResult() }
        func sendIssuesAtomically(_ records: [CloudKitMappedRecord]) async throws -> CloudProgressSendResult {
            CloudProgressSendResult()
        }
        func fetchReviewHistory(for identity: QuestionIdentity) async throws -> [CloudKitMappedRecord] { [] }
        func resetPendingChanges() async {}
    }

    private func session(_ id: String, at seconds: TimeInterval) -> SessionDetail {
        SessionDetail(
            sessionID: id,
            completedAt: Date(timeIntervalSince1970: seconds),
            answers: [SessionAnswer(courseID: "course", packID: "pack", questionID: "q-1", correct: true)]
        )
    }

    func testSavingAtTheRetentionCapStillPersistsADecodableCheckpoint() async throws {
        let cap = ProgressEnvelope.operationRetention
        let base: TimeInterval = 2_000_000
        // Within the 30-day window so only the count cap applies.
        let operations = (1...cap).map { index -> ProgressOperation in
            ProgressOperation(
                operationID: "op-\(index)",
                createdAt: Date(timeIntervalSince1970: base + Double(index)),
                status: .applied,
                session: session("session-\(index)", at: base + Double(index)),
                serverRevision: index
            )
        }
        let envelope = ProgressEnvelope(
            documentRevision: cap,
            actorID: "device-a",
            operationID: "op-\(cap)",
            aggregate: AggregateSnapshot(sessionsTotal: cap, answered: cap, correct: cap),
            operations: operations
        )
        let store = CloudProgressMemoryStore(checkpoint: CloudProgressCheckpoint(
            envelope: envelope,
            sentOperationIDs: Set(operations.map(\.id))
        ))
        let repository = try CloudProgressRepository(actorID: "device-a", persistence: store, transport: IdleTransport())

        let saved = try await repository.save(
            session("session-new", at: base + Double(cap) + 10),
            operationID: "op-new",
            now: Date(timeIntervalSince1970: base + Double(cap) + 10)
        )

        let checkpoint = await repository.checkpointSnapshot()
        XCTAssertLessThanOrEqual(checkpoint.envelope.operations.count, cap)
        XCTAssertTrue(checkpoint.envelope.operations.contains { $0.id == saved.id }, "the new unsent operation is kept")
        XCTAssertFalse(checkpoint.envelope.operations.contains { $0.id == "op-1" }, "the oldest acknowledged operation is dropped")
        // The persisted bytes must load again on the next launch.
        let data = try JSONEncoder().encode(checkpoint)
        XCTAssertNoThrow(try JSONDecoder().decode(CloudProgressCheckpoint.self, from: data))
    }
}
