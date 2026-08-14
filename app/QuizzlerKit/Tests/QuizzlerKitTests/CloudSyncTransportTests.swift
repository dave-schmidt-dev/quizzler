import Foundation
import CloudKit
import XCTest
@testable import QuizzlerKit

final class CloudSyncTransportTests: XCTestCase {
    func testProbeFailureStatusesAreDistinctAndPrivacySafe() {
        let failures: [(CloudSyncProbeError, String)] = [
            (.competingWriteFailed, "competing_write_failed"),
            (.accountStatusFailed, "account_status_failed"),
            (.fetchChangesFailed, "fetch_changes_failed"),
            (.sendChangesFailed, "send_changes_failed"),
            (.savingZoneFailed, "saving_zone_failed"),
            (.savingRecordFailed, "saving_record_failed"),
            (.conflictSendFailed, "conflict_send_failed"),
            (.conflictFetchFailed, "conflict_fetch_failed"),
            (.replaySendFailed, "replay_send_failed"),
            (.deletingRecordFailed, "deleting_record_failed"),
            (.deletingZoneFailed, "deleting_zone_failed"),
            (.conflictNotObserved, "conflict_not_observed"),
            (.replayNotAcknowledged, "replay_not_acknowledged")
        ]

        XCTAssertEqual(Set(failures.map { $0.1 }).count, failures.count)
        for (error, expectedStatus) in failures {
            XCTAssertEqual(error.redactedStatus, expectedStatus)
            XCTAssertFalse(error.redactedStatus.contains("CK"))
            XCTAssertFalse(error.redactedStatus.contains("iCloud"))
            XCTAssertFalse(error.redactedStatus.contains("/"))
        }
    }

    func testCloudKitServerRecordChangedNSErrorIsRecognizedByDomainAndCode() {
        let error = NSError(
            domain: CKErrorDomain,
            code: CKError.Code.serverRecordChanged.rawValue
        )

        XCTAssertTrue(cloudSyncProbeIsServerRecordChanged(error))
        XCTAssertFalse(cloudSyncProbeIsServerRecordChanged(
            NSError(domain: "OtherErrorDomain", code: CKError.Code.serverRecordChanged.rawValue)
        ))
        XCTAssertFalse(cloudSyncProbeIsServerRecordChanged(
            NSError(domain: CKErrorDomain, code: CKError.Code.networkFailure.rawValue)
        ))
    }

    func testCloudKitPartialFailureRecognizesNestedServerRecordChangedNSError() {
        let nestedConflict = NSError(
            domain: CKErrorDomain,
            code: CKError.Code.serverRecordChanged.rawValue
        )
        let partialFailure = NSError(
            domain: CKErrorDomain,
            code: CKError.Code.partialFailure.rawValue,
            userInfo: [CKPartialErrorsByItemIDKey: ["opaque-item": nestedConflict]]
        )

        XCTAssertTrue(cloudSyncProbeIsServerRecordChanged(partialFailure))
    }

    func testCloudKitPartialFailureRejectsUnrelatedNestedNSError() {
        let nestedFailure = NSError(
            domain: CKErrorDomain,
            code: CKError.Code.networkFailure.rawValue
        )
        let partialFailure = NSError(
            domain: CKErrorDomain,
            code: CKError.Code.partialFailure.rawValue,
            userInfo: [CKPartialErrorsByItemIDKey: ["opaque-item": nestedFailure]]
        )

        XCTAssertFalse(cloudSyncProbeIsServerRecordChanged(partialFailure))
        XCTAssertFalse(cloudSyncProbeIsServerRecordChanged(
            NSError(domain: "OtherErrorDomain", code: CKError.Code.partialFailure.rawValue)
        ))
    }

    func testCloudKitEntitlementAndAccountNSErrorsAreRecognizedWithoutSurfacingDetails() {
        let codes: [CKError.Code] = [
            .missingEntitlement,
            .notAuthenticated,
            .accountTemporarilyUnavailable
        ]

        for code in codes {
            XCTAssertTrue(cloudSyncProbeIsEntitlementOrAccount(
                NSError(domain: CKErrorDomain, code: code.rawValue)
            ))
        }
        XCTAssertFalse(cloudSyncProbeIsEntitlementOrAccount(
            NSError(domain: CKErrorDomain, code: CKError.Code.networkFailure.rawValue)
        ))
        XCTAssertFalse(cloudSyncProbeIsEntitlementOrAccount(
            NSError(domain: "OtherErrorDomain", code: CKError.Code.notAuthenticated.rawValue)
        ))
    }

    func testStatePersistenceFailureIsTerminalAndNotComplete() {
        var gate = CloudSyncProbePersistenceGate()
        XCTAssertNoThrow(try gate.throwIfFailed())

        gate.markFailed()

        XCTAssertThrowsError(try gate.throwIfFailed()) { error in
            XCTAssertEqual(error as? CloudSyncProbeError, .statePersistenceFailed)
        }
    }

    func testCompletionRequiresAcknowledgedStatePersistence() {
        var gate = CloudSyncProbePersistenceGate()
        XCTAssertThrowsError(try gate.throwIfNotReadyForCompletion())

        gate.markPersisted()
        // A delayed pre-delete persistence cannot certify completion.
        gate.markTerminalDeletionAcknowledged()
        XCTAssertThrowsError(try gate.throwIfNotReadyForCompletion()) {
            XCTAssertEqual($0 as? CloudSyncProbeError, .statePersistenceFailed)
        }

        gate.markPersisted()
        XCTAssertNoThrow(try gate.throwIfNotReadyForCompletion())

        gate.markFailed()
        XCTAssertThrowsError(try gate.throwIfNotReadyForCompletion()) { error in
        XCTAssertEqual(error as? CloudSyncProbeError, .statePersistenceFailed)
        }
    }

    func testTerminalPersistenceRequiresAStateUpdateAfterDeleteAcknowledgement() {
        var gate = CloudSyncProbePersistenceGate()
        gate.markPersisted()
        gate.markPersisted() // delayed update from an earlier lifecycle step
        gate.markTerminalDeletionAcknowledged()

        XCTAssertThrowsError(try gate.throwIfNotReadyForCompletion())
        gate.markPersisted() // serialization that follows the exact delete ack
        XCTAssertNoThrow(try gate.throwIfNotReadyForCompletion())
    }

    func testZoneDeletionRequiresExactSuccessfulAcknowledgement() {
        var acknowledgement = CloudSyncZoneDeletionAcknowledgement<String>()
        acknowledgement.begin(for: "probe")

        _ = acknowledgement.receive(deletedZones: ["probe"], failedZones: [])

        XCTAssertNoThrow(try acknowledgement.throwIfDeleted(for: "probe"))
    }

    func testZoneDeletionFailsClosedWhenCloudKitReportsFailure() {
        var acknowledgement = CloudSyncZoneDeletionAcknowledgement<String>()
        acknowledgement.begin(for: "probe")

        _ = acknowledgement.receive(deletedZones: [], failedZones: ["probe"])

        XCTAssertThrowsError(try acknowledgement.throwIfDeleted(for: "probe")) { error in
            XCTAssertEqual(error as? CloudSyncProbeError, .disposableZoneCleanupFailed)
        }
    }

    func testZoneDeletionRejectsUnexpectedZoneAcknowledgement() {
        var acknowledgement = CloudSyncZoneDeletionAcknowledgement<String>()
        acknowledgement.begin(for: "probe")

        _ = acknowledgement.receive(deletedZones: ["probe", "other"], failedZones: [])

        XCTAssertThrowsError(try acknowledgement.throwIfDeleted(for: "probe")) { error in
            XCTAssertEqual(error as? CloudSyncProbeError, .disposableZoneCleanupFailed)
        }
    }

    func testZoneDeletionIgnoresUnrelatedDatabaseChangeEventUntilExactAck() {
        var acknowledgement = CloudSyncZoneDeletionAcknowledgement<String>()
        acknowledgement.begin(for: "probe")

        _ = acknowledgement.receive(deletedZones: ["other"], failedZones: [])
        XCTAssertThrowsError(try acknowledgement.throwIfDeleted(for: "probe"))

        _ = acknowledgement.receive(deletedZones: ["probe"], failedZones: [])
        XCTAssertNoThrow(try acknowledgement.throwIfDeleted(for: "probe"))
    }

    func testTimeoutReturnsWithoutWaitingForAnUncooperativeOperation() async throws {
        let recorder = ProgressRecorder()
        let clock = ContinuousClock()
        let started = clock.now

        do {
            _ = try await cloudSyncProbeBounded(
                timeout: .milliseconds(10),
                operation: {
                    await withUnsafeContinuation { (_: UnsafeContinuation<Void, Never>) in }
                    return ()
                },
                onTimeout: {
                    recorder.append("operation_timed_out")
                }
            )
            XCTFail("timed operation unexpectedly completed")
        } catch let error as CloudSyncProbeError {
            XCTAssertEqual(error, .operationTimedOut)
        }

        XCTAssertEqual(recorder.values, ["operation_timed_out"])
        XCTAssertLessThan(clock.now - started, .seconds(1))
    }

    func testCancellationReturnsWithoutWaitingForAnUncooperativeOperation() async throws {
        let recorder = ProgressRecorder()
        let task = Task {
            try await cloudSyncProbeBounded(
                timeout: .seconds(10),
                operation: {
                    await withUnsafeContinuation { (_: UnsafeContinuation<Void, Never>) in }
                    return ()
                },
                onTimeout: { recorder.append("operation_timed_out") },
                onCancellation: { recorder.append("operation_cancelled") }
            )
        }
        try await Task.sleep(for: .milliseconds(10))
        task.cancel()

        do {
            _ = try await task.value
            XCTFail("cancelled operation unexpectedly completed")
        } catch let error as CloudSyncProbeError {
            XCTAssertEqual(error, .operationCancelled)
        }
        XCTAssertEqual(recorder.values, ["operation_cancelled"])
    }

    func testCancellationBeforeContinuationInstallationReturnsImmediately() async throws {
        let recorder = ProgressRecorder()
        let task = Task {
            try await cloudSyncProbeBounded(
                timeout: .seconds(10),
                operation: {
                    await withUnsafeContinuation { (_: UnsafeContinuation<Void, Never>) in }
                    return ()
                },
                onTimeout: { recorder.append("operation_timed_out") },
                onCancellation: { recorder.append("operation_cancelled") }
            )
        }
        task.cancel()

        do {
            _ = try await task.value
            XCTFail("pre-start cancelled operation unexpectedly completed")
        } catch let error as CloudSyncProbeError {
            XCTAssertEqual(error, .operationCancelled)
        }
        XCTAssertEqual(recorder.values, ["operation_cancelled"])
    }

    func testTerminalPersistenceWaiterCancellationReturnsOperationCancelled() async throws {
        let delegate = CKSyncEngineTransport.Delegate(
            stateStore: TestStateStore(),
            progress: { _ in }
        )
        let task = Task {
            try await delegate.awaitTerminalStatePersistence()
        }
        try await Task.sleep(for: .milliseconds(10))
        task.cancel()

        do {
            try await task.value
            XCTFail("cancelled terminal-persistence wait unexpectedly completed")
        } catch let error as CloudSyncProbeError {
            XCTAssertEqual(error, .operationCancelled)
        }
    }

    func testDevelopmentProbeDisablesAutomaticEngineScheduling() {
        XCTAssertFalse(CloudSyncDevelopmentProbe.automaticallySync)
    }

    func testRelaunchPreservesDurableIntent() throws {
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

    func testServerConflictReplayPreservesLocalIntentAtNewRevision() {
        var ledger = CloudSyncProbeLedger(
            pendingChanges: [.init(recordName: "lifecycle", operation: .save, revision: 3, payloadHash: "local-replay")],
            serverRevision: 3
        )

        // A competing server write advances the change tag; replay rebases
        // the local intent instead of discarding it or overwriting blindly.
        ledger.rebase(on: 4)

        XCTAssertEqual(ledger.pendingChanges, [
            .init(recordName: "lifecycle", operation: .save, revision: 5, payloadHash: "local-replay")
        ])
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

private final class ProgressRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var storedValues: [String] = []

    var values: [String] {
        lock.withLock { storedValues }
    }

    func append(_ value: String) {
        lock.withLock { storedValues.append(value) }
    }
}

private struct TestStateStore: CloudSyncEngineStatePersisting {
    func load() throws -> Data? { nil }
    func save(_ data: Data) throws {}
}
