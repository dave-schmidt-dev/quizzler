import Foundation
import XCTest
@testable import QuizzlerKit

/// The authoritative snapshot prunes applied operations older than the
/// retention window, while the device keeps its own acknowledged copies. A full
/// fetch must treat those pruned-but-acknowledged operations as already
/// represented by the snapshot, not replay them as stale (INV-9).
final class CloudProgressRetentionPruneTests: XCTestCase {
    private final class FullSnapshotTransport: @unchecked Sendable, CloudProgressTransport {
        let result: CloudProgressFetchResult
        init(_ result: CloudProgressFetchResult) { self.result = result }
        func fetchChanges() async throws -> CloudProgressFetchResult { result }
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

    func testFullFetchAfterServerPrunedAnAcknowledgedOperationDoesNotConflict() async throws {
        let day: TimeInterval = 86_400
        let start: TimeInterval = 1_000_000
        let old = ProgressOperation(
            operationID: "op-day-0",
            createdAt: Date(timeIntervalSince1970: start),
            status: .applied,
            session: session("session-day-0", at: start),
            serverRevision: 1
        )
        var local = ProgressEnvelope(
            documentRevision: 1,
            actorID: "device-a",
            operationID: old.id,
            sessionDetails: [old.session].compactMap { $0 },
            aggregate: AggregateSnapshot(sessionsTotal: 1, answered: 1, correct: 1),
            operations: [old]
        )
        local.schemaVersion = ProgressEnvelope.currentSchemaVersion

        // Another device saves on day 31; the server applies it and prunes day 0.
        let recent = ProgressOperation(
            operationID: "op-day-31",
            createdAt: Date(timeIntervalSince1970: start + 31 * day),
            status: .applied,
            session: session("session-day-31", at: start + 31 * day),
            serverRevision: 31
        )
        var remote = local
        remote.applying(recent)
        remote.documentRevision = 31
        remote.operationID = recent.id
        XCTAssertFalse(remote.operations.contains { $0.id == old.id }, "premise: the server pruned the day-0 operation")

        let store = CloudProgressMemoryStore(checkpoint: CloudProgressCheckpoint(
            envelope: local,
            snapshotChangeTag: "tag-1",
            sentOperationIDs: [old.id]
        ))
        let transport = FullSnapshotTransport(CloudProgressFetchResult(
            records: [try CloudKitMapping.snapshotRecord(remote)],
            snapshotChangeTag: "tag-31",
            isFullSnapshot: true
        ))
        let repository = try CloudProgressRepository(actorID: "device-a", persistence: store, transport: transport)

        _ = try await repository.fetch(full: true)

        let merged = await repository.snapshot()
        XCTAssertEqual(merged.documentRevision, 31)
        XCTAssertEqual(merged.operationID, recent.id)
        let checkpoint = await repository.checkpointSnapshot()
        XCTAssertFalse(checkpoint.requiresRebase, "the fetch must not be left in a rebase loop")

        // A second full fetch must stay clean as well (the loop never converged).
        _ = try await repository.fetch(full: true)
        let again = await repository.checkpointSnapshot()
        XCTAssertFalse(again.requiresRebase)
        XCTAssertEqual(again.envelope.documentRevision, 31)
    }
}
