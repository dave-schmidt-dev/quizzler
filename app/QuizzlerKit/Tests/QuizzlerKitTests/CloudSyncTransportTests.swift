import XCTest
@testable import QuizzlerKit

final class CloudSyncTransportTests: XCTestCase {
    func testAutomaticSyncRelaunchPreservesDurableIntent() throws {
        var ledger = CloudSyncProbeLedger()
        ledger.enqueue(.init(recordName: "lifecycle", operation: .save, revision: 1, payloadHash: "abc"))

        let restored = try CloudSyncProbeLedger.restored(from: ledger.serialized())

        XCTAssertEqual(restored.pendingChanges, ledger.pendingChanges)
        XCTAssertEqual(CloudSyncDevelopmentProbe.zoneName, "QuizzlerDevelopmentProbe-v1")
    }

    func testFourHundredRecordBatching() {
        let changes = (0..<801).map {
            CloudSyncPendingChange(recordName: "r-\($0)", operation: .save, revision: 1)
        }
        let ledger = CloudSyncProbeLedger(pendingChanges: changes)

        XCTAssertEqual(ledger.batches().map(\.count), [400, 400, 1])
    }

    func testPendingOperationCompactionKeepsLatestIntent() {
        var ledger = CloudSyncProbeLedger()
        ledger.enqueue(.init(recordName: "same", operation: .save, revision: 1, payloadHash: "first"))
        ledger.enqueue(.init(recordName: "same", operation: .delete, revision: 2))
        ledger.enqueue(.init(recordName: "other", operation: .save, revision: 1, payloadHash: "second"))

        XCTAssertEqual(ledger.pendingChanges, [
            .init(recordName: "same", operation: .delete, revision: 2),
            .init(recordName: "other", operation: .save, revision: 1, payloadHash: "second")
        ])
    }

    func testCrashBetweenDeleteAndEngineStatePersistenceReplaysDelete() throws {
        var ledger = CloudSyncProbeLedger()
        ledger.enqueue(.init(recordName: "lifecycle", operation: .save, revision: 1, payloadHash: "abc"))
        let beforeDeleteEngineState = try ledger.serialized()

        // The delete is committed to app data before CKSyncEngine receives it.
        ledger.enqueue(.init(recordName: "lifecycle", operation: .delete, revision: 2))
        let durableDeleteIntent = try ledger.serialized()
        XCTAssertNotEqual(beforeDeleteEngineState, durableDeleteIntent)

        let relaunched = try CloudSyncProbeLedger.restored(from: durableDeleteIntent)
        XCTAssertEqual(relaunched.pendingChanges, [.init(recordName: "lifecycle", operation: .delete, revision: 2)])
    }

    func testStaleClientRebaseReplaysAtServerRevision() {
        var ledger = CloudSyncProbeLedger(
            pendingChanges: [.init(recordName: "lifecycle", operation: .save, revision: 2, payloadHash: "abc")],
            serverRevision: 1
        )

        ledger.rebase(on: 7)

        XCTAssertEqual(ledger.serverRevision, 7)
        XCTAssertEqual(ledger.pendingChanges, [.init(recordName: "lifecycle", operation: .save, revision: 8, payloadHash: "abc")])
    }

    func testAcknowledgementDoesNotRemoveNewerCompactedIntent() {
        var ledger = CloudSyncProbeLedger()
        let sent = CloudSyncPendingChange(recordName: "lifecycle", operation: .save, revision: 1, payloadHash: "abc")
        ledger.enqueue(sent)
        ledger.enqueue(.init(recordName: "lifecycle", operation: .delete, revision: 2))

        ledger.acknowledge([sent])

        XCTAssertEqual(ledger.pendingChanges, [.init(recordName: "lifecycle", operation: .delete, revision: 2)])
    }
}
