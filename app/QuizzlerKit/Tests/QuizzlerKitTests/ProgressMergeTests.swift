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

    private func levelOperation(_ id: String, level: Int, base: Int = 0, revision: Int) -> ProgressMergeOperation {
        ProgressMergeOperation(
            operationID: id,
            baseRevision: base,
            serverRevision: revision,
            createdAt: epoch,
            serverRecordedAt: epoch,
            maximumLeitnerLevel: level
        )
    }

    func testLevelLimitOrdersWithReviewsByServerRevisionAndReplaysIdempotently() throws {
        let identity = QuestionIdentity(courseID: "course", packID: "pack", questionID: "ordered")
        let limit = levelOperation("limit-2", level: 2, revision: 1)
        let review = operation("review", base: 0, revision: 2, answers: [SessionAnswer(identity: identity, correct: true)])
        let first = try ProgressMergeEngine.merge([review, limit], into: .empty(actorID: "device"))

        XCTAssertEqual(first.snapshot.envelope.maximumLeitnerLevel, 2)
        XCTAssertEqual(first.snapshot.envelope.srs.first?.state.tier, 2)
        let replay = try ProgressMergeEngine.merge([limit], into: first.snapshot)
        XCTAssertEqual(replay.duplicateOperationIDs, ["limit-2"])
        XCTAssertEqual(replay.snapshot, first.snapshot)
    }

    func testMergeProducesTheSameCapturedTimeEventsAsLocalReducerAndReplayProducesNone() throws {
        let identity = QuestionIdentity(courseID: "course", packID: "pack", questionID: "event-time")
        let firstAnswerAt = epoch.addingTimeInterval(-90)
        let secondAnswerAt = epoch.addingTimeInterval(-30)
        let session = SessionDetail(
            sessionID: "event-session",
            completedAt: epoch,
            answers: [
                SessionAnswer(identity: identity, correct: true, answeredAt: firstAnswerAt),
                SessionAnswer(identity: identity, correct: false, answeredAt: secondAnswerAt)
            ]
        )
        let remote = ProgressMergeOperation(
            operationID: "event-op",
            baseRevision: 0,
            serverRevision: 1,
            createdAt: epoch,
            updatedAt: epoch.addingTimeInterval(300),
            serverRecordedAt: epoch,
            session: session
        )
        var local = ProgressOperation(
            operationID: remote.operationID,
            createdAt: remote.createdAt,
            status: .applied,
            session: session
        )
        local.serverRevision = remote.serverRevision
        local.updatedAt = remote.updatedAt
        let baseline = ProgressEnvelope(actorID: "device")
        let localEvents = ProgressEnvelope.reviewEvents(for: local, from: baseline)
        let merged = try ProgressMergeEngine.merge([remote], into: try ProgressMergeSnapshot(envelope: baseline))

        XCTAssertEqual(merged.reviewEvents, localEvents)
        XCTAssertEqual(merged.reviewEvents.map(\.eventTime), [firstAnswerAt, secondAnswerAt])
        XCTAssertEqual(merged.reviewEvents.map(\.serverRevision), [1, 1])
        XCTAssertEqual(merged.snapshot.envelope.srs.first?.state.lastReviewedAt, secondAnswerAt)

        let replay = try ProgressMergeEngine.merge([remote], into: merged.snapshot)
        XCTAssertEqual(replay.duplicateOperationIDs, ["event-op"])
        XCTAssertTrue(replay.reviewEvents.isEmpty)
    }

    func testV1HighTierBaselineMergeRecordsTruePriorAndReplayIsIdle() throws {
        let reviewedAt = epoch.addingTimeInterval(-2 * 86_400)
        let dueAt = reviewedAt.addingTimeInterval(120 * 86_400)
        let tierSix = QuestionIdentity(courseID: "course", packID: "pack", questionID: "legacy-six")
        let tierSeven = QuestionIdentity(courseID: "course", packID: "pack", questionID: "legacy-seven")
        let baseline = ProgressEnvelope(
            schemaVersion: 1,
            actorID: "device",
            srs: [
                SRSSnapshot(identity: tierSix, state: try SRSState(
                    tier: 6, nextDueAt: dueAt, lastReviewedAt: reviewedAt, intervalDays: 60, reviewCount: 6
                )),
                SRSSnapshot(identity: tierSeven, state: try SRSState(
                    tier: 7, nextDueAt: dueAt, lastReviewedAt: reviewedAt, intervalDays: 120, reviewCount: 7
                ))
            ]
        )
        let answeredAt = epoch.addingTimeInterval(-30)
        let answers = [
            SessionAnswer(identity: tierSix, correct: true, answeredAt: answeredAt),
            SessionAnswer(identity: tierSeven, correct: false, answeredAt: answeredAt)
        ]
        let remote = operation("legacy-review", revision: 1, answers: answers)
        var local = ProgressOperation(
            operationID: remote.operationID,
            createdAt: remote.createdAt,
            status: .applied,
            session: session("s-legacy-review", answers: answers)
        )
        local.serverRevision = remote.serverRevision
        local.updatedAt = remote.updatedAt
        let localEvents = ProgressEnvelope.reviewEvents(for: local, from: baseline)
        XCTAssertEqual(localEvents.map(\.priorLevel), [6, 7])
        XCTAssertEqual(localEvents.map(\.resultingLevel), [5, 3])
        XCTAssertEqual(localEvents.map(\.resultingDueAt), [
            answeredAt.addingTimeInterval(30 * 86_400),
            answeredAt.addingTimeInterval(7 * 86_400)
        ])
        var localEnvelope = baseline
        _ = localEnvelope.applying(local)

        let merged = try ProgressMergeEngine.merge([remote], into: try ProgressMergeSnapshot(envelope: baseline))
        XCTAssertEqual(merged.reviewEvents, localEvents)
        XCTAssertEqual(merged.snapshot.envelope.srs, localEnvelope.srs)
        XCTAssertEqual(merged.snapshot.envelope.srs.map(\.state.tier), [5, 3])

        let replay = try ProgressMergeEngine.merge([remote], into: merged.snapshot)
        XCTAssertEqual(replay.duplicateOperationIDs, ["legacy-review"])
        XCTAssertTrue(replay.reviewEvents.isEmpty)
        XCTAssertEqual(replay.snapshot, merged.snapshot)
    }

    func testCapEventsAreIdentityOrderedAndExcludeUnaffectedQuestions() throws {
        let early = Date(timeIntervalSince1970: 10)
        let highA = QuestionIdentity(courseID: "course", packID: "a", questionID: "q")
        let highB = QuestionIdentity(courseID: "course", packID: "b", questionID: "q")
        let unaffected = QuestionIdentity(courseID: "course", packID: "z", questionID: "q")
        var envelope = ProgressEnvelope(
            actorID: "device",
            srs: [
                SRSSnapshot(identity: highB, state: try SRSState(tier: 5, nextDueAt: early.addingTimeInterval(30 * 86_400), lastReviewedAt: early, intervalDays: 30, reviewCount: 4)),
                SRSSnapshot(identity: unaffected, state: try SRSState(tier: 2, nextDueAt: early.addingTimeInterval(3 * 86_400), lastReviewedAt: early, intervalDays: 3, reviewCount: 1)),
                SRSSnapshot(identity: highA, state: try SRSState(tier: 4, nextDueAt: early.addingTimeInterval(14 * 86_400), lastReviewedAt: early, intervalDays: 14, reviewCount: 3))
            ],
            maximumLeitnerLevel: 5
        )
        var cap = ProgressOperation(
            operationID: "cap-op",
            createdAt: epoch,
            status: .applied,
            kind: .setMaximumLeitnerLevel,
            maximumLeitnerLevel: 3
        )
        cap.serverRevision = 7
        let events = envelope.applying(cap)

        XCTAssertEqual(events.map(\.identity), [highA, highB])
        XCTAssertEqual(events.map(\.ordinal), [0, 1])
        XCTAssertEqual(events.map(\.outcome), [.maximumLevelChanged, .maximumLevelChanged])
        XCTAssertEqual(events.map(\.serverRevision), [7, 7])
    }

    func testConcurrentLevelLimitsUseServerRevisionOrder() throws {
        let higher = levelOperation("higher", level: 7, revision: 10)
        let lower = levelOperation("lower", level: 3, revision: 11)
        let result = try ProgressMergeEngine.merge([lower, higher], into: .empty(actorID: "device"))

        XCTAssertEqual(result.snapshot.envelope.maximumLeitnerLevel, 3)
        XCTAssertEqual(result.appliedOperationIDs, ["higher", "lower"])
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

    func testFullShapeCanonicalEvidenceHasGoldenBytesAndFacadeHash() throws {
        let identity = QuestionIdentity(courseID: "course", packID: "pack", questionID: "canonical")
        let answeredAt = epoch.addingTimeInterval(-120)
        let completedAt = epoch.addingTimeInterval(-60)
        let session = SessionDetail(
            sessionID: "session-canonical-0001",
            completedAt: completedAt,
            answers: [SessionAnswer(identity: identity, correct: true, answeredAt: answeredAt)]
        )
        let issue = try QuestionIssue(
            issueID: "issue-canonical-0001",
            courseID: "course",
            packID: "pack",
            questionID: "canonical",
            questionType: .multipleChoice,
            appVersion: "1.0.0",
            build: "100",
            selectedResponse: "B",
            description: "Synthetic canonical evidence"
        )
        let reviewCreatedAt = epoch.addingTimeInterval(-300)
        let capCreatedAt = epoch.addingTimeInterval(-200)
        let review = ProgressMergeOperation(
            operationID: "op-review-0001",
            baseRevision: 0,
            baseOperationID: "envelope-base-0001",
            serverRevision: 1,
            createdAt: reviewCreatedAt,
            updatedAt: reviewCreatedAt,
            serverRecordedAt: reviewCreatedAt,
            session: session
        )
        let cap = ProgressMergeOperation(
            operationID: "op-cap-0001",
            baseRevision: 1,
            baseOperationID: "op-review-0001",
            serverRevision: 2,
            createdAt: capCreatedAt,
            updatedAt: capCreatedAt,
            serverRecordedAt: capCreatedAt,
            maximumLeitnerLevel: 4
        )
        let reviewEnvelopeOperation = ProgressOperation(
            operationID: review.operationID,
            createdAt: review.createdAt,
            status: .encodedSizeRefused,
            kind: .review,
            session: session,
            error: .encodedSizeRefused,
            serverRevision: review.serverRevision
        )
        let capEnvelopeOperation = ProgressOperation(
            operationID: cap.operationID,
            createdAt: cap.createdAt,
            status: .failed,
            kind: .setMaximumLeitnerLevel,
            maximumLeitnerLevel: cap.maximumLeitnerLevel,
            error: .failed("synthetic details"),
            serverRevision: cap.serverRevision
        )
        let envelope = ProgressEnvelope(
            schemaVersion: 2,
            documentRevision: 2,
            actorID: "canonical-device",
            operationID: "op-cap-0001",
            createdAt: epoch.addingTimeInterval(-600),
            sessionDetails: [session],
            aggregate: AggregateSnapshot(sessionsTotal: 1, answered: 1, correct: 1),
            mastery: [MasterySnapshot(identity: identity, answered: 3, correct: 2)],
            srs: [SRSSnapshot(identity: identity, state: try SRSState(
                tier: 4,
                nextDueAt: epoch.addingTimeInterval(7 * 86_400),
                lastReviewedAt: epoch.addingTimeInterval(-90),
                intervalDays: 7,
                reviewCount: 3
            ))],
            maximumLeitnerLevel: 4,
            compaction: ProgressCompaction(version: 1, watermarkRevision: 0),
            operations: [reviewEnvelopeOperation, capEnvelopeOperation],
            issues: [issue]
        )
        let snapshot = try ProgressMergeSnapshot(
            envelope: envelope,
            operations: [review, cap],
            tombstones: [ProgressMergeTombstone(operationID: "op-compacted-0001", serverRevision: 1)]
        )

        let data = try snapshot.canonicalSemanticData()
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let canonicalEnvelope = try XCTUnwrap(object["envelope"] as? [String: Any])
        XCTAssertEqual(canonicalEnvelope["maximumLeitnerLevel"] as? Int, 4)
        let canonicalSessions = try XCTUnwrap(canonicalEnvelope["sessionDetails"] as? [[String: Any]])
        XCTAssertEqual(canonicalSessions.first?["id"] as? String, "session-canonical-0001")
        let canonicalAnswers = try XCTUnwrap(canonicalSessions.first?["answers"] as? [[String: Any]])
        XCTAssertEqual(canonicalAnswers.first?["answeredAt"] as? Int, 1_799_999_880_000)
        let canonicalMastery = try XCTUnwrap(canonicalEnvelope["mastery"] as? [[String: Any]])
        XCTAssertEqual(canonicalMastery.first?["answered"] as? Int, 3)
        XCTAssertEqual(canonicalMastery.first?["correct"] as? Int, 2)
        let canonicalSRS = try XCTUnwrap(canonicalEnvelope["srs"] as? [[String: Any]])
        XCTAssertEqual(canonicalSRS.first?["lastReviewedAt"] as? Int, 1_799_999_910_000)
        XCTAssertEqual(canonicalSRS.first?["tier"] as? Int, 4)
        let canonicalIssues = try XCTUnwrap(canonicalEnvelope["issues"] as? [[String: Any]])
        let canonicalIssue = try XCTUnwrap(canonicalIssues.first)
        XCTAssertEqual(canonicalIssue["schemaVersion"] as? Int, 1)
        XCTAssertEqual(canonicalIssue["issueID"] as? String, "issue-canonical-0001")
        XCTAssertEqual(canonicalIssue["courseID"] as? String, "course")
        XCTAssertEqual(canonicalIssue["packID"] as? String, "pack")
        XCTAssertEqual(canonicalIssue["questionID"] as? String, "canonical")
        XCTAssertEqual(canonicalIssue["questionType"] as? String, "multiple_choice")
        XCTAssertEqual(canonicalIssue["appVersion"] as? String, "1.0.0")
        XCTAssertEqual(canonicalIssue["build"] as? String, "100")
        XCTAssertEqual(canonicalIssue["selectedResponse"] as? String, "B")
        XCTAssertEqual(canonicalIssue["description"] as? String, "Synthetic canonical evidence")

        let canonicalEnvelopeOperations = try XCTUnwrap(canonicalEnvelope["operations"] as? [[String: Any]])
        XCTAssertEqual(canonicalEnvelopeOperations.compactMap { $0["id"] as? String }, ["op-cap-0001", "op-review-0001"])
        XCTAssertEqual(canonicalEnvelopeOperations.map { ($0["kind"] as? String) ?? "review" }, [
            "set_maximum_leitner_level", "review"
        ])
        XCTAssertEqual(canonicalEnvelopeOperations.compactMap { $0["error"] as? String }, [
            "failed", "encoded_size_refused"
        ])

        let canonicalOperations = try XCTUnwrap(object["operations"] as? [[String: Any]])
        XCTAssertEqual(canonicalOperations.map { $0["baseOperationID"] as? String }, [
            "envelope-base-0001", "op-review-0001"
        ])
        XCTAssertEqual(canonicalOperations.map { ($0["kind"] as? String) ?? "review" }, [
            "review", "set_maximum_leitner_level"
        ])
        XCTAssertEqual(canonicalOperations.compactMap { $0["maximumLeitnerLevel"] as? Int }, [4])
        let canonicalTombstones = try XCTUnwrap(object["tombstones"] as? [[String: Any]])
        XCTAssertEqual(canonicalTombstones.first?["operationID"] as? String, "op-compacted-0001")
        XCTAssertEqual(canonicalTombstones.first?["serverRevision"] as? Int, 1)

        XCTAssertEqual(try snapshot.canonicalSemanticHash(), "32d3956df02b9a84814ed438716663eff92396022e95603765d6028c63977565")
        XCTAssertEqual(
            try ProgressMergeEngine.canonicalEvidenceHash(snapshot),
            try snapshot.canonicalSemanticHash()
        )
    }
}
