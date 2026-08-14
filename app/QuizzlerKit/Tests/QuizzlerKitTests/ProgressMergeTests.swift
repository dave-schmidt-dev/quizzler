import XCTest
@testable import QuizzlerKit

final class ProgressMergeTests: XCTestCase {
    private let epoch = Date(timeIntervalSince1970: 1_800_000_000)

    private func session(
        _ id: String,
        at offset: TimeInterval = 0,
        answers: [SessionAnswer] = []
    ) -> SessionDetail {
        SessionDetail(sessionID: id, completedAt: epoch.addingTimeInterval(offset), answers: answers)
    }

    private func operation(
        _ id: String,
        base: Int = 0,
        revision: Int,
        offset: TimeInterval = 0,
        answers: [SessionAnswer] = []
    ) -> ProgressMergeOperation {
        let date = epoch.addingTimeInterval(offset)
        return ProgressMergeOperation(
            operationID: id,
            baseRevision: base,
            serverRevision: revision,
            createdAt: date,
            serverRecordedAt: date,
            session: session("s-\(id)", at: offset, answers: answers)
        )
    }

    func testEqualRevisionUsesUTF8OperationIDAndIgnoresClockSkew() throws {
        let first = operation("z", revision: 7, offset: -10_000)
        let second = operation("a", revision: 7, offset: 10_000)
        let result = try ProgressMergeEngine.merge(
            [first, second],
            into: .empty(actorID: "device")
        )

        XCTAssertEqual(result.appliedOperationIDs, ["a", "z"])
        XCTAssertEqual(result.snapshot.envelope.documentRevision, 7)
        XCTAssertEqual(result.snapshot.envelope.sessionDetails.map(\.id), ["s-a", "s-z"])
    }

    func testReplayIsIdempotentAndChangedPayloadIsRejected() throws {
        let original = operation("same", revision: 1)
        let first = try ProgressMergeEngine.merge([original], into: .empty(actorID: "device"))
        let replay = try ProgressMergeEngine.merge([original], into: first.snapshot)

        XCTAssertEqual(replay.appliedOperationIDs, [])
        XCTAssertEqual(replay.duplicateOperationIDs, ["same"])
        XCTAssertEqual(replay.snapshot, first.snapshot)

        let changed = ProgressMergeOperation(
            operationID: "same",
            baseRevision: 0,
            serverRevision: 1,
            createdAt: epoch,
            session: session("different")
        )
        XCTAssertThrowsError(try ProgressMergeEngine.merge([changed], into: first.snapshot)) {
            XCTAssertEqual($0 as? ProgressMergeError, .duplicateOperationPayloadMismatch)
        }

        let changedRevision = ProgressMergeOperation(
            operationID: "same",
            baseRevision: 0,
            serverRevision: 2,
            createdAt: epoch,
            session: original.session
        )
        XCTAssertThrowsError(try ProgressMergeEngine.merge([changedRevision], into: first.snapshot)) {
            XCTAssertEqual($0 as? ProgressMergeError, .duplicateOperationPayloadMismatch)
        }

        let changedCreatedAt = ProgressMergeOperation(
            operationID: "same",
            baseRevision: 0,
            serverRevision: 1,
            createdAt: epoch.addingTimeInterval(1),
            session: original.session
        )
        XCTAssertThrowsError(try ProgressMergeEngine.merge([changedCreatedAt], into: first.snapshot)) {
            XCTAssertEqual($0 as? ProgressMergeError, .duplicateOperationPayloadMismatch)
        }
    }

    func testStaleBaseCanMergeRemotelyButSendRequiresRebase() throws {
        let first = operation("first", base: 0, revision: 1)
        let second = operation("second", base: 0, revision: 2)
        let result = try ProgressMergeEngine.merge([first, second], into: .empty(actorID: "device"))
        XCTAssertTrue(result.rebased)
        let third = operation("third", base: 0, revision: 3)
        XCTAssertThrowsError(try ProgressMergeEngine.merge(
            [third], into: result.snapshot, requireBaseRevision: true
        )) {
            XCTAssertEqual(
                $0 as? ProgressMergeError,
                .revisionConflict(currentRevision: 2, operationBaseRevision: 0)
            )
        }
    }

    func testSendBaseRequiresMatchingRevisionAndOperationID() throws {
        let first = operation("first", base: 0, revision: 1)
        let baseline = try ProgressMergeEngine.merge(
            [first],
            into: .empty(actorID: "device"),
            now: epoch
        ).snapshot
        let chained = ProgressMergeOperation(
            operationID: "second",
            baseRevision: 1,
            baseOperationID: "first",
            serverRevision: 2,
            createdAt: epoch,
            serverRecordedAt: epoch,
            session: session("s-second")
        )
        XCTAssertNoThrow(try ProgressMergeEngine.merge(
            [chained], into: baseline, requireBaseRevision: true, now: epoch
        ))

        let wrongOrder = ProgressMergeOperation(
            operationID: "third",
            baseRevision: 1,
            baseOperationID: "other",
            serverRevision: 2,
            createdAt: epoch,
            serverRecordedAt: epoch,
            session: session("s-third")
        )
        XCTAssertThrowsError(try ProgressMergeEngine.merge(
            [wrongOrder], into: baseline, requireBaseRevision: true, now: epoch
        )) {
            XCTAssertEqual(
                $0 as? ProgressMergeError,
                .orderingConflict(currentRevision: 1, currentOperationID: "first")
            )
        }
    }

    func testMultiPackAnswersAndTwoHundredSessionBoundaryArePreserved() throws {
        let answers = [
            SessionAnswer(courseID: "cissp", packID: "security", questionID: "q1", correct: true),
            SessionAnswer(courseID: "cissp", packID: "network", questionID: "q1", correct: false)
        ]
        var operations = [operation("op-000", revision: 1, answers: answers)]
        operations.append(contentsOf: (1...200).map {
            operation(String(format: "op-%03d", $0), revision: $0 + 1)
        })
        let result = try ProgressMergeEngine.merge(operations, into: .empty(actorID: "device"))

        XCTAssertEqual(result.snapshot.envelope.sessionDetails.count, 200)
        XCTAssertFalse(result.snapshot.envelope.sessionDetails.contains(where: { $0.id == "s-op-000" }))
        XCTAssertEqual(result.snapshot.envelope.aggregate.sessionsTotal, 201)
        XCTAssertEqual(result.snapshot.envelope.aggregate.answered, 2)
        XCTAssertEqual(result.snapshot.envelope.mastery.count, 2)
        XCTAssertEqual(Set(result.snapshot.envelope.mastery.map(\.identity.packID)), ["security", "network"])
    }

    func testCompactionHonorsThirtyDayBoundaryAndKeepsPending() throws {
        let now = epoch
        let atBoundary = operation("boundary", revision: 1, offset: -31 * 86_400)
        // Rebase the operation timestamp independently of its session date.
        let exact = ProgressMergeOperation(
            operationID: "exact",
            baseRevision: 0,
            serverRevision: 2,
            createdAt: now.addingTimeInterval(-ProgressMergeLimits.maximumOperationAge),
            updatedAt: now.addingTimeInterval(-ProgressMergeLimits.maximumOperationAge),
            serverRecordedAt: now.addingTimeInterval(-ProgressMergeLimits.maximumOperationAge),
            session: session("exact")
        )
        let fresh = operation("fresh", revision: 3, offset: 0)
        let pending = operation("pending", revision: 4)
        let source = try ProgressMergeSnapshot(
            envelope: ProgressEnvelope(actorID: "device"),
            operations: [atBoundary, exact, fresh, pending]
        )
        let plan = try ProgressCompactor.plan(
            snapshot: source,
            now: now,
            pendingOperationIDs: ["pending"]
        )

        XCTAssertTrue(plan.snapshot.operations.contains(where: { $0.operationID == "exact" }))
        XCTAssertTrue(plan.snapshot.operations.contains(where: { $0.operationID == "fresh" }))
        XCTAssertTrue(plan.snapshot.operations.contains(where: { $0.operationID == "pending" }))
        XCTAssertFalse(plan.snapshot.operations.contains(where: { $0.operationID == "boundary" }))
        XCTAssertEqual(plan.snapshot.watermarkRevision, 1)
        XCTAssertFalse(plan.snapshot.tombstones.contains(where: { $0.operationID == "boundary" }))
    }

    func testCompactionCountLimitAndStrictSub250Batches() throws {
        let operations = (1...4_300).map { index in
            operation(String(format: "op-%05d", index), revision: index, offset: -86_400)
        }
        let source = try ProgressMergeSnapshot(
            envelope: ProgressEnvelope(
                documentRevision: 4_300,
                actorID: "device",
                operationID: "op-04300"
            ),
            operations: operations
        )
        let plan = try ProgressCompactor.plan(
            snapshot: source,
            now: epoch,
            pendingOperationIDs: []
        )

        XCTAssertEqual(plan.snapshot.operations.count, ProgressMergeLimits.maximumOperationRecords)
        XCTAssertEqual(plan.deletedOperationIDs.count, 4_300 - ProgressMergeLimits.maximumOperationRecords)
        XCTAssertTrue(plan.snapshot.operations.contains { $0.serverRevision == 4_300 })
        XCTAssertFalse(plan.deletedOperationIDs.contains("op-04200"))
        XCTAssertTrue(plan.deletedOperationIDs.contains("op-00001"))
        XCTAssertTrue(plan.deleteBatches.allSatisfy { $0.count < 250 })
        XCTAssertEqual(plan.deleteBatches.flatMap { $0 }.count, plan.deletedOperationIDs.count)
        let roundTrip = try JSONDecoder().decode(
            ProgressMergeSnapshot.self,
            from: JSONEncoder().encode(plan.snapshot)
        )
        XCTAssertEqual(roundTrip, plan.snapshot)
    }

    func testOldCompactedReplayUsesTombstoneWithoutMutatingSnapshot() throws {
        let source = try ProgressMergeSnapshot(
            envelope: ProgressEnvelope(
                documentRevision: 10,
                actorID: "device",
                compaction: ProgressCompaction(watermarkRevision: 10)
            ),
            tombstones: [ProgressMergeTombstone(operationID: "old", serverRevision: 3)]
        )
        let old = operation("old", revision: 3)
        let result = try ProgressMergeEngine.merge([old], into: source)
        XCTAssertEqual(result.duplicateOperationIDs, ["old"])
        XCTAssertEqual(result.snapshot, source)
    }

    func testIrreconcilableDuplicateRequiresExplicitManualResolution() throws {
        let leftOperation = operation("conflict", revision: 1)
        let rightOperation = ProgressMergeOperation(
            operationID: "conflict",
            baseRevision: 0,
            serverRevision: 1,
            createdAt: epoch,
            session: session("other")
        )
        let left = try ProgressMergeSnapshot(
            envelope: ProgressEnvelope(actorID: "device"),
            operations: [leftOperation]
        )
        let right = try ProgressMergeSnapshot(
            envelope: ProgressEnvelope(actorID: "device"),
            operations: [rightOperation]
        )
        XCTAssertThrowsError(try ProgressMergeEngine.reconcile(local: left, remote: right)) {
            XCTAssertEqual($0 as? ProgressMergeError, .manualResolutionRequired)
        }
    }

    func testCanonicalEvidenceHashIsInvariantToSeededOperationPermutation() throws {
        let baselineEnvelope = ProgressEnvelope(
            documentRevision: 0,
            actorID: "device",
            operationID: "base",
            createdAt: epoch
        )
        let baseline = try ProgressMergeSnapshot(envelope: baselineEnvelope)
        let seeded = [
            operation("z", revision: 3, offset: -100),
            operation("a", revision: 1, offset: 100),
            operation("m", revision: 2, offset: -1_000)
        ]
        let permutations = [
            seeded,
            [seeded[2], seeded[0], seeded[1]],
            [seeded[1], seeded[2], seeded[0]]
        ]
        let hashes = try permutations.map {
            try ProgressMergeEngine.merge($0, into: baseline, now: epoch).snapshot.canonicalEvidenceHash()
        }
        XCTAssertEqual(Set(hashes).count, 1)
        XCTAssertEqual(hashes.first?.count, 64)
        XCTAssertEqual(
            hashes.first,
            "309c0426c3d737539bba4d869c1dbd9ea6b1dbb916ca403c153f8116306be4ad"
        )

        let changed = operation("a", revision: 1, answers: [
            SessionAnswer(courseID: "cissp", packID: "security", questionID: "different", correct: true)
        ])
        let changedHash = try ProgressMergeEngine.merge([changed], into: baseline, now: epoch)
            .snapshot.canonicalEvidenceHash()
        XCTAssertNotEqual(hashes.first, changedHash)
    }

    func testIndependentBuildsAtDifferentMergeTimesHaveTheSameHash() throws {
        let baseline = ProgressMergeSnapshot.empty(actorID: "device", createdAt: epoch)
        let seeded = [
            operation("one", revision: 1, offset: -10),
            operation("two", base: 1, revision: 2, offset: 20)
        ]
        let first = try ProgressMergeEngine.merge(seeded, into: baseline, now: epoch)
        let second = try ProgressMergeEngine.merge(
            seeded,
            into: baseline,
            now: epoch.addingTimeInterval(86_400)
        )

        XCTAssertEqual(first.snapshot, second.snapshot)
        XCTAssertEqual(
            try first.snapshot.canonicalEvidenceHash(),
            try second.snapshot.canonicalEvidenceHash()
        )
    }

    func testLateLowerRevisionFailsClosed() throws {
        let first = try ProgressMergeEngine.merge(
            [operation("newer", revision: 5)],
            into: .empty(actorID: "device"),
            now: epoch
        ).snapshot

        XCTAssertThrowsError(try ProgressMergeEngine.merge(
            [operation("late", revision: 3)],
            into: first,
            now: epoch
        )) {
            XCTAssertEqual(
                $0 as? ProgressMergeError,
                .revisionConflict(currentRevision: 5, operationBaseRevision: 0)
            )
        }
    }

    func testFoldedEnvelopeOperationMustHaveLogOrTombstoneEvidence() throws {
        let operation = ProgressOperation(
            operationID: "folded",
            createdAt: epoch,
            status: .applied,
            session: session("folded")
        )
        var assigned = operation
        assigned.serverRevision = 1
        let envelope = ProgressEnvelope(
            documentRevision: 1,
            actorID: "device",
            operationID: operation.id,
            createdAt: epoch,
            operations: [assigned]
        )

        XCTAssertThrowsError(try ProgressMergeSnapshot(envelope: envelope)) {
            XCTAssertEqual($0 as? ProgressMergeError, .invalidSnapshot)
        }
    }

    func testFoldedOperationRevisionChangeFailsClosed() throws {
        let operation = ProgressOperation(
            operationID: "folded",
            createdAt: epoch,
            status: .applied,
            session: session("folded")
        )
        let envelope = ProgressEnvelope(
            documentRevision: 0,
            actorID: "device",
            operationID: "baseline",
            createdAt: epoch,
            operations: [operation]
        )
        let snapshot = try ProgressMergeSnapshot(envelope: envelope)
        let changedRevision = ProgressMergeOperation(
            operationID: operation.id,
            baseRevision: 0,
            serverRevision: 1,
            createdAt: epoch,
            session: session("folded")
        )
        XCTAssertThrowsError(try ProgressMergeEngine.merge([changedRevision], into: snapshot)) {
            XCTAssertEqual($0 as? ProgressMergeError, .duplicateOperationPayloadMismatch)
        }
    }

    func testWatermarkBoundsTombstones() throws {
        let source = try ProgressMergeSnapshot(
            envelope: ProgressEnvelope(
                documentRevision: 3,
                actorID: "device",
                operationID: "new",
                createdAt: epoch,
                compaction: ProgressCompaction(watermarkRevision: 2)
            ),
            operations: [operation("new", revision: 3)],
            tombstones: [
                ProgressMergeTombstone(operationID: "old-1", serverRevision: 1),
                ProgressMergeTombstone(operationID: "old-2", serverRevision: 2),
                ProgressMergeTombstone(operationID: "future", serverRevision: 4)
            ]
        )

        let plan = try ProgressCompactor.plan(snapshot: source, now: epoch)

        XCTAssertEqual(plan.snapshot.tombstones.map(\.operationID), ["future"])
    }

    func testEmptySnapshotCanonicalHashIsDeterministic() throws {
        let first = ProgressMergeSnapshot.empty(actorID: "device")
        let second = ProgressMergeSnapshot.empty(actorID: "device")
        let firstHash = try first.canonicalEvidenceHash()
        XCTAssertEqual(firstHash, try second.canonicalEvidenceHash())
        XCTAssertEqual(
            firstHash,
            "c9e89a38e54660d79c1c86ac65d2d7e231915c3dd3bf63140285fb71d7fe0731"
        )
    }

    func testCompactionDoesNotAdvanceWatermarkPastEqualRevisionRetainedPair() throws {
        let old = operation("old", revision: 7, offset: -40 * 86_400)
        let fresh = operation("fresh", revision: 7)
        let source = try ProgressMergeSnapshot(
            envelope: ProgressEnvelope(actorID: "device"),
            operations: [old, fresh]
        )

        let plan = try ProgressCompactor.plan(snapshot: source, now: epoch, pendingOperationIDs: [])

        XCTAssertEqual(plan.snapshot.operations.map(\.operationID), ["fresh"])
        XCTAssertEqual(plan.snapshot.watermarkRevision, 0)
        XCTAssertTrue(plan.snapshot.tombstones.contains { $0.operationID == "old" })
    }

    func testPendingOperationGuardRunsAgainstFullRetentionSet() throws {
        let operations = (1...4_097).map { index in
            operation(String(format: "op-%05d", index), revision: index, offset: -86_400)
        }
        let source = try ProgressMergeSnapshot(
            envelope: ProgressEnvelope(actorID: "device"),
            operations: operations
        )

        XCTAssertThrowsError(try ProgressCompactor.plan(
            snapshot: source,
            now: epoch,
            pendingOperationIDs: ["op-00001"]
        )) {
            XCTAssertEqual($0 as? ProgressMergeError, .pendingOperationWouldBePruned)
        }
    }

    func testTombstonesCannotExceedBoundWhenWatermarkIsPinned() throws {
        let operations = (2...(ProgressMergeLimits.maximumOperationRecords * 2 + 2)).map { revision in
            operation("op-\(revision)", revision: revision, offset: -86_400)
        }
        let source = try ProgressMergeSnapshot(
            envelope: ProgressEnvelope(actorID: "device"),
            operations: operations
        )

        XCTAssertThrowsError(try ProgressCompactor.plan(snapshot: source, now: epoch)) {
            XCTAssertEqual($0 as? ProgressMergeError, .tombstoneRetentionExceeded)
        }
    }

    func testMalformedDecodedSnapshotFailsInsteadOfCrashingOnDuplicateIDs() throws {
        let source = try ProgressMergeSnapshot(
            envelope: ProgressEnvelope(actorID: "device"),
            operations: [operation("one", revision: 1)]
        )
        let encoded = try JSONEncoder().encode(source)
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: encoded) as? [String: Any])
        let operations = try XCTUnwrap(object["operations"] as? [[String: Any]])
        object["operations"] = [operations[0], operations[0]]
        let malformed = try JSONSerialization.data(withJSONObject: object)

        XCTAssertThrowsError(try JSONDecoder().decode(ProgressMergeSnapshot.self, from: malformed))
    }
}
