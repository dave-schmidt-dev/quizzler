import XCTest
@testable import QuizzlerKit

final class SyncRecoveryTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_900_000_000)

    private func operation(_ id: String, revision: Int, offset: TimeInterval = 0) -> ProgressMergeOperation {
        let date = now.addingTimeInterval(offset)
        return ProgressMergeOperation(
            operationID: id,
            baseRevision: max(0, revision - 1),
            serverRevision: revision,
            createdAt: date,
            serverRecordedAt: date,
            session: SessionDetail(sessionID: "session-\(id)", completedAt: date, answers: [])
        )
    }

    func testCompactionStagesAreCrashResumable() throws {
        let old = operation("old", revision: 1, offset: -40 * 86_400)
        let incoming = operation("new", revision: 2)
        let source = try ProgressMergeSnapshot(
            envelope: ProgressEnvelope(actorID: "device"),
            operations: [old]
        )
        let initial = try SyncRecoveryMachine.begin(snapshot: source, pendingOperations: [incoming], now: now)
        let expected = try SyncRecoveryMachine.finish(initial)
        let expectedHash = try expected.snapshot.canonicalEvidenceHash()
        var checkpoint = initial
        var crashStages = Set<SyncRecoveryStage>()
        while checkpoint.stage != .completed {
            let stage = checkpoint.stage
            if !crashStages.contains(stage) {
                crashStages.insert(stage)
                XCTAssertThrowsError(try SyncRecoveryMachine.step(checkpoint, injectCrashAt: stage)) {
                    XCTAssertEqual($0 as? SyncRecoveryError, .crashInjected(stage))
                }
            }
            checkpoint = try SyncRecoveryMachine.step(checkpoint)
            _ = try JSONDecoder().decode(
                SyncRecoveryCheckpoint.self,
                from: JSONEncoder().encode(checkpoint)
            )
        }

        XCTAssertEqual(checkpoint.stage, .completed)
        XCTAssertEqual(checkpoint.engineStateGeneration, 1)
        XCTAssertTrue(checkpoint.snapshot.operations.contains(where: { $0.operationID == "new" }))
        XCTAssertFalse(checkpoint.snapshot.tombstones.contains(where: { $0.operationID == "old" }))
        XCTAssertEqual(try checkpoint.snapshot.canonicalEvidenceHash(), expectedHash)
        XCTAssertEqual(crashStages, [
            .appendBeforeCompact,
            .snapshotPublished,
            .watermarkAdvanced,
            .deleteBatch(0),
            .engineStatePersisted
        ])
    }

    func testRecoveryStageTransitionsPublishThenAdvanceThenDeleteThenPersist() throws {
        let old = operation("old", revision: 1, offset: -40 * 86_400)
        let source = try ProgressMergeSnapshot(
            envelope: ProgressEnvelope(actorID: "device"),
            operations: [old]
        )
        let initial = try SyncRecoveryMachine.begin(snapshot: source, pendingOperations: [], now: now)
        let originalWatermark = initial.snapshot.watermarkRevision

        let published = try SyncRecoveryMachine.step(initial)
        XCTAssertEqual(published.stage, .snapshotPublished)
        XCTAssertEqual(published.snapshot.watermarkRevision, originalWatermark)
        XCTAssertEqual(published.snapshotPublicationGeneration, 1)
        XCTAssertEqual(published.watermarkGeneration, 0)

        let advanced = try SyncRecoveryMachine.step(published)
        XCTAssertEqual(advanced.stage, .watermarkAdvanced)
        XCTAssertGreaterThan(advanced.snapshot.watermarkRevision, originalWatermark)
        XCTAssertEqual(advanced.snapshotPublicationGeneration, 1)
        XCTAssertEqual(advanced.watermarkGeneration, 1)
        XCTAssertTrue(advanced.deletedOperationIDs.isEmpty)

        let deleting = try SyncRecoveryMachine.step(advanced)
        XCTAssertEqual(deleting.stage, .deleteBatch(0))
        let afterDelete = try SyncRecoveryMachine.step(deleting)
        XCTAssertEqual(afterDelete.stage, .engineStatePersisted)
        XCTAssertEqual(afterDelete.deletedOperationIDs, ["old"])

        let completed = try SyncRecoveryMachine.step(afterDelete)
        XCTAssertEqual(completed.stage, .completed)
        XCTAssertEqual(completed.engineStateGeneration, 1)
    }

    func testEachIndividualCrashPointConvergesToTheSameCanonicalHash() throws {
        let old = operation("old", revision: 1, offset: -40 * 86_400)
        let incoming = operation("new", revision: 2)
        let source = try ProgressMergeSnapshot(
            envelope: ProgressEnvelope(actorID: "device"),
            operations: [old]
        )
        let initial = try SyncRecoveryMachine.begin(snapshot: source, pendingOperations: [incoming], now: now)
        let expected = try SyncRecoveryMachine.finish(initial)
        let expectedHash = try expected.snapshot.canonicalEvidenceHash()

        var stages: [SyncRecoveryStage] = []
        var cursor = initial
        while cursor.stage != .completed {
            stages.append(cursor.stage)
            cursor = try SyncRecoveryMachine.step(cursor)
        }

        for crashStage in stages {
            var checkpoint = initial
            while checkpoint.stage != .completed {
                if checkpoint.stage == crashStage {
                    XCTAssertThrowsError(try SyncRecoveryMachine.step(checkpoint, injectCrashAt: crashStage)) {
                        XCTAssertEqual($0 as? SyncRecoveryError, .crashInjected(crashStage))
                    }
                }
                checkpoint = try SyncRecoveryMachine.step(checkpoint)
            }
            XCTAssertEqual(try checkpoint.snapshot.canonicalEvidenceHash(), expectedHash)
        }
    }

    func testAppendHappensBeforeCompactionAndPendingIsNeverDeleted() throws {
        let old = operation("old", revision: 1, offset: -40 * 86_400)
        let pending = operation("pending", revision: 2)
        let source = try ProgressMergeSnapshot(
            envelope: ProgressEnvelope(actorID: "device"),
            operations: [old]
        )
        let checkpoint = try SyncRecoveryMachine.begin(
            snapshot: source,
            pendingOperations: [pending],
            pendingEngineOperationIDs: ["pending"],
            now: now
        )

        XCTAssertEqual(checkpoint.stage, .appendBeforeCompact)
        XCTAssertTrue(checkpoint.snapshot.operations.contains(where: { $0.operationID == "pending" }))
        let finished = try SyncRecoveryMachine.finish(checkpoint)
        XCTAssertTrue(finished.snapshot.operations.contains(where: { $0.operationID == "pending" }))
    }

    func testExpiredTokenRebasesFromFullSnapshotAndReplaysPending() throws {
        let full = try ProgressMergeSnapshot(
            envelope: ProgressEnvelope(
                documentRevision: 100,
                actorID: "device",
                compaction: ProgressCompaction(watermarkRevision: 100)
            ),
            tombstones: [ProgressMergeTombstone(operationID: "old", serverRevision: 50)]
        )
        let pending = operation("pending", revision: 101)
        let checkpoint = try SyncRecoveryMachine.recoverAfterTokenExpiry(
            fullSnapshot: full,
            pendingOperations: [pending],
            now: now
        )

        XCTAssertTrue(checkpoint.tokenExpired)
        XCTAssertEqual(checkpoint.snapshot.envelope.documentRevision, 101)
        XCTAssertTrue(checkpoint.snapshot.operations.contains(where: { $0.operationID == "pending" }))
        XCTAssertThrowsError(try SyncRecoveryMachine.requireFreshBase(
            snapshot: checkpoint.snapshot,
            suppliedBaseRevision: 100
        )) {
            XCTAssertEqual($0 as? SyncRecoveryError, .rebaseRequired(currentRevision: 101, suppliedRevision: 100))
        }
        try SyncRecoveryMachine.requireFreshBase(
            snapshot: checkpoint.snapshot,
            suppliedBaseRevision: 101,
            suppliedOperationID: checkpoint.snapshot.envelope.operationID
        )
    }

    func testCheckpointRoundTripPreservesAssociatedDeleteStage() throws {
        let source = try ProgressMergeSnapshot(envelope: ProgressEnvelope(actorID: "device"))
        let checkpoint = try SyncRecoveryMachine.begin(snapshot: source, pendingOperations: [], now: now)
        let encoded = try JSONEncoder().encode(checkpoint)
        let decoded = try JSONDecoder().decode(SyncRecoveryCheckpoint.self, from: encoded)
        XCTAssertEqual(decoded, checkpoint)
    }

    func testDurableCheckpointFailureAtEveryStageReloadsPreviousValue() throws {
        let old = operation("old", revision: 1, offset: -40 * 86_400)
        let source = try ProgressMergeSnapshot(
            envelope: ProgressEnvelope(actorID: "device"),
            operations: [old]
        )
        var checkpoint = try SyncRecoveryMachine.begin(snapshot: source, pendingOperations: [], now: now)
        let store = RecoveryCheckpointStore(checkpoint)

        while checkpoint.stage != .completed {
            let next = try SyncRecoveryMachine.step(checkpoint)
            store.failNextSave = true
            XCTAssertThrowsError(try store.save(next))
            XCTAssertEqual(store.load(), checkpoint)
            try store.save(next)
            checkpoint = try XCTUnwrap(store.load())
        }
        XCTAssertEqual(checkpoint.engineStateGeneration, 1)
    }
}

private final class RecoveryCheckpointStore {
    private var value: SyncRecoveryCheckpoint?
    var failNextSave = false

    init(_ value: SyncRecoveryCheckpoint) { self.value = value }

    func load() -> SyncRecoveryCheckpoint? { value }

    func save(_ value: SyncRecoveryCheckpoint) throws {
        if failNextSave {
            failNextSave = false
            throw SyncRecoveryError.invalidCheckpoint
        }
        self.value = value
    }
}
