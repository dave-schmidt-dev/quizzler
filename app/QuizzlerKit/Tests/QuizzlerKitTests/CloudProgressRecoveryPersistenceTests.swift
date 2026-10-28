import Foundation
import XCTest
@testable import QuizzlerKit

/// A one-shot CloudKit recovery event whose checkpoint write fails must not be
/// forgotten (INV-9): the isolation and rebase gates stay enforced until the
/// recovery is durably recorded.
final class CloudProgressRecoveryPersistenceTests: XCTestCase {
    private final class TogglingStore: @unchecked Sendable, CloudProgressPersistence {
        private let lock = NSLock()
        private var checkpoint: CloudProgressCheckpoint?
        private var failing = false

        func setFailing(_ value: Bool) { lock.withLock { failing = value } }
        func load() throws -> CloudProgressCheckpoint? { lock.withLock { checkpoint } }
        func save(_ checkpoint: CloudProgressCheckpoint) throws {
            try lock.withLock {
                if failing { throw CloudProgressRepositoryError.statePersistenceFailed }
                self.checkpoint = checkpoint
            }
        }
        var persisted: CloudProgressCheckpoint? { lock.withLock { checkpoint } }
    }

    private final class CountingTransport: @unchecked Sendable, CloudProgressTransport {
        private let lock = NSLock()
        private var fetches = 0
        private var sends = 0
        var networkCalls: Int { lock.withLock { fetches + sends } }

        func fetchChanges() async throws -> CloudProgressFetchResult {
            lock.withLock { fetches += 1 }
            return CloudProgressFetchResult()
        }
        func sendChanges(_ records: [CloudKitMappedRecord]) async throws -> CloudProgressSendResult {
            lock.withLock { sends += 1 }
            return CloudProgressSendResult()
        }
        func sendProgressAtomically(
            _ records: [CloudKitMappedRecord], expectedRevision: Int, snapshotChangeTag: String?
        ) async throws -> CloudProgressSendResult {
            lock.withLock { sends += 1 }
            return CloudProgressSendResult()
        }
        func sendIssuesAtomically(_ records: [CloudKitMappedRecord]) async throws -> CloudProgressSendResult {
            lock.withLock { sends += 1 }
            return CloudProgressSendResult()
        }
        func fetchReviewHistory(for identity: QuestionIdentity) async throws -> [CloudKitMappedRecord] { [] }
        func resetPendingChanges() async {}
    }

    func testAccountChangeWithFailingCheckpointWriteStillBlocksFetchAndSend() async throws {
        let store = TogglingStore()
        let transport = CountingTransport()
        let repository = try CloudProgressRepository(actorID: "actor-a", persistence: store, transport: transport)

        store.setFailing(true)
        try await repository.handle(.accountChanged)

        // The disk is still failing: nothing may reach the network.
        do {
            _ = try await repository.fetch()
            XCTFail("fetch ran with the account-change gate unset")
        } catch {
            XCTAssertEqual(error as? CloudProgressRepositoryError, .statePersistenceFailed)
        }
        do {
            _ = try await repository.send()
            XCTFail("send ran with the account-change gate unset")
        } catch {
            XCTAssertEqual(error as? CloudProgressRepositoryError, .statePersistenceFailed)
        }
        XCTAssertEqual(transport.networkCalls, 0)

        // The disk recovers: the remembered recovery is applied and persisted
        // before anything else, so the isolation gate is now set on disk too.
        store.setFailing(false)
        do {
            _ = try await repository.send()
            XCTFail("send must remain blocked by account isolation")
        } catch {
            XCTAssertEqual(error as? CloudProgressRepositoryError, .accountIsolationRequired)
        }
        XCTAssertEqual(store.persisted?.accountIsolationRequired, true)
        XCTAssertEqual(store.persisted?.requiresRebase, true)
        XCTAssertEqual(transport.networkCalls, 0)
    }

    func testSuccessfulAccountChangeLeavesNoPendingRecovery() async throws {
        let store = TogglingStore()
        let transport = CountingTransport()
        let repository = try CloudProgressRepository(actorID: "actor-a", persistence: store, transport: transport)

        try await repository.handle(.accountChanged)

        XCTAssertEqual(store.persisted?.accountIsolationRequired, true)
        do {
            _ = try await repository.send()
            XCTFail("send must be gated by account isolation")
        } catch {
            XCTAssertEqual(error as? CloudProgressRepositoryError, .accountIsolationRequired)
        }
    }
}
