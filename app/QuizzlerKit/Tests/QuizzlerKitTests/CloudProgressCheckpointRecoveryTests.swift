import Foundation
import XCTest
@testable import QuizzlerKit

/// A corrupt or unreadable launch-time checkpoint must never become a permanent
/// launch crash loop (INV-1): corruption is quarantined and sync rebases.
final class CloudProgressCheckpointRecoveryTests: XCTestCase {
    private struct UnusedTransport: CloudProgressTransport {
        func fetchChanges() async throws -> CloudProgressFetchResult { throw CloudProgressRepositoryError.persistenceUnavailable }
        func sendChanges(_ records: [CloudKitMappedRecord]) async throws -> CloudProgressSendResult {
            throw CloudProgressRepositoryError.persistenceUnavailable
        }
        func sendProgressAtomically(
            _ records: [CloudKitMappedRecord], expectedRevision: Int, snapshotChangeTag: String?
        ) async throws -> CloudProgressSendResult { throw CloudProgressRepositoryError.persistenceUnavailable }
        func sendIssuesAtomically(_ records: [CloudKitMappedRecord]) async throws -> CloudProgressSendResult {
            throw CloudProgressRepositoryError.persistenceUnavailable
        }
        func fetchReviewHistory(for identity: QuestionIdentity) async throws -> [CloudKitMappedRecord] { [] }
        func resetPendingChanges() async {}
    }

    private func scratchDirectory() throws -> URL {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("QuizzlerKit-recovery-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: directory) }
        return directory
    }

    func testCorruptCheckpointIsQuarantinedAndReplacedByRebaseRequiredCheckpoint() async throws {
        let directory = try scratchDirectory()
        let url = directory.appendingPathComponent("cloud-progress-v1.json")
        let corrupt = Data("{truncated".utf8)
        try corrupt.write(to: url)

        let store = CloudProgressFileStore(url: url)
        let outcome = try store.loadRecoveringCorruption(actorID: "actor-a")

        let quarantined = try XCTUnwrap(outcome.quarantinedURL)
        XCTAssertEqual(try Data(contentsOf: quarantined), corrupt, "the unreadable bytes are preserved, not deleted")
        let fresh = try XCTUnwrap(outcome.checkpoint)
        XCTAssertTrue(fresh.requiresRebase)
        XCTAssertEqual(fresh.envelope.actorID, "actor-a")
        // The replacement is durable and the next launch loads it normally.
        let reloaded = try XCTUnwrap(try store.load())
        XCTAssertEqual(reloaded, fresh)
        XCTAssertNil(try store.loadRecoveringCorruption(actorID: "actor-a").quarantinedURL)
        // The repository starts from it instead of throwing.
        let repository = try CloudProgressRepository(actorID: "actor-a", persistence: store, transport: UnusedTransport())
        let started = await repository.checkpointSnapshot()
        XCTAssertTrue(started.requiresRebase)
    }

    func testActorMismatchIsQuarantinedInsteadOfThrowingAtLaunch() throws {
        let directory = try scratchDirectory()
        let url = directory.appendingPathComponent("cloud-progress-v1.json")
        let store = CloudProgressFileStore(url: url)
        try store.save(CloudProgressCheckpoint(envelope: ProgressEnvelope(actorID: "old-actor")))

        let outcome = try store.loadRecoveringCorruption(actorID: "new-actor")

        XCTAssertNotNil(outcome.quarantinedURL)
        XCTAssertEqual(outcome.checkpoint?.envelope.actorID, "new-actor")
        XCTAssertEqual(outcome.checkpoint?.requiresRebase, true)
    }

    func testHealthyAndMissingCheckpointsAreUntouched() throws {
        let directory = try scratchDirectory()
        let url = directory.appendingPathComponent("cloud-progress-v1.json")
        let store = CloudProgressFileStore(url: url)

        let missing = try store.loadRecoveringCorruption(actorID: "actor-a")
        XCTAssertNil(missing.checkpoint)
        XCTAssertNil(missing.quarantinedURL)

        let healthy = CloudProgressCheckpoint(envelope: ProgressEnvelope(actorID: "actor-a"))
        try store.save(healthy)
        let loaded = try store.loadRecoveringCorruption(actorID: "actor-a")
        XCTAssertEqual(loaded.checkpoint, healthy)
        XCTAssertNil(loaded.quarantinedURL)
    }

    func testUnreadableFileIsNotQuarantinedAndStaysRetryable() throws {
        let directory = try scratchDirectory()
        let url = directory.appendingPathComponent("cloud-progress-v1.json")
        // A directory at the checkpoint path cannot be read as data.
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: false)

        XCTAssertThrowsError(try CloudProgressFileStore(url: url).loadRecoveringCorruption(actorID: "actor-a")) { error in
            XCTAssertEqual(error as? CloudProgressRepositoryError, .persistenceUnavailable)
        }
        var isDirectory: ObjCBool = false
        XCTAssertTrue(FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory))
        XCTAssertTrue(isDirectory.boolValue, "an unreadable path is never moved or replaced")
    }
}
