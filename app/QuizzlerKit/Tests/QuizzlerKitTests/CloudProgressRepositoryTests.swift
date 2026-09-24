import Foundation
import XCTest
@testable import QuizzlerKit

final class CloudProgressRepositoryTests: XCTestCase {
    private func session(_ id: String = "session-1") -> SessionDetail {
        SessionDetail(
            sessionID: id,
            completedAt: Date(timeIntervalSince1970: 1_000),
            answers: [SessionAnswer(courseID: "course", packID: "pack", questionID: "q-1", correct: true)]
        )
    }

    private func timedSession(_ id: String, answers: [SessionAnswer]? = nil) -> SessionDetail {
        SessionDetail(
            sessionID: id,
            completedAt: Date(timeIntervalSince1970: 2_000),
            answers: answers ?? [SessionAnswer(
                courseID: "course", packID: "pack", questionID: "q-1",
                correct: true, answeredAt: Date(timeIntervalSince1970: 1_500)
            )]
        )
    }

    private func issue(_ id: String = "issue-1") throws -> QuestionIssue {
        try QuestionIssue(
            issueID: id,
            courseID: "course",
            packID: "pack",
            questionID: "q-1",
            questionType: .multipleChoice,
            appVersion: "1.0.0",
            build: "100",
            description: "The explanation is inconsistent."
        )
    }

    private func reviewEvent(
        operationID: String = "review-operation",
        ordinal: Int = 0,
        revision: Int = 1
    ) -> QuestionReviewEvent {
        QuestionReviewEvent(
            operationID: operationID,
            ordinal: ordinal,
            identity: QuestionIdentity(courseID: "course", packID: "pack", questionID: "q-1"),
            eventTime: Date(timeIntervalSince1970: 1_000 + Double(ordinal)),
            outcome: .correct,
            priorLevel: 1,
            resultingLevel: 2,
            resultingDueAt: Date(timeIntervalSince1970: 87_400 + Double(ordinal)),
            serverRevision: revision
        )
    }

    private func makeRepository(
        transport: FakeTransport = FakeTransport(),
        store: CloudProgressMemoryStore = CloudProgressMemoryStore()
    ) throws -> (CloudProgressRepository, FakeTransport, CloudProgressMemoryStore) {
        let repository = try CloudProgressRepository(
            actorID: "device-a",
            persistence: store,
            transport: transport,
            retryPolicy: .init(baseDelayMilliseconds: 10, maximumDelayMilliseconds: 100, maximumAttempts: 3)
        )
        return (repository, transport, store)
    }

    func testMaximumLeitnerLevelIsACloudProgressOperation() async throws {
        let (repository, _, _) = try makeRepository()
        let operation = try await repository.setMaximumLeitnerLevel(3, operationID: "limit-3")

        XCTAssertEqual(operation.kind, .setMaximumLeitnerLevel)
        XCTAssertEqual(operation.maximumLeitnerLevel, 3)
        let maximum = await repository.maximumLeitnerLevel()
        XCTAssertEqual(maximum, 3)
        let records = try await repository.pendingRecords()
        XCTAssertTrue(records.contains { $0.recordName == "ProgressOperation/limit-3" })
        XCTAssertTrue(records.contains { $0.recordName == CloudKitContract.snapshotRecordName })
    }

    func testNewCloudReviewUpgradesSnapshotSchemaWhileLegacyReplayStaysCompatible() async throws {
        let (repository, _, _) = try makeRepository()
        let before = await repository.snapshot()
        XCTAssertEqual(before.schemaVersion, 1)

        _ = try await repository.save(session("new-review"))
        let after = await repository.snapshot()
        XCTAssertEqual(after.schemaVersion, 2)
        let records = try await repository.pendingRecords()
        let snapshotRecord = try XCTUnwrap(records.first { $0.kind == .snapshot })
        XCTAssertEqual(snapshotRecord.fields["schema_version"], .integer(2))
        XCTAssertEqual(try CloudKitMapping.snapshot(from: snapshotRecord).schemaVersion, 2)
    }

    func testMaximumLevelOperationFetchedByAnotherRepository() async throws {
        let (source, _, _) = try makeRepository()
        let (destination, _, _) = try makeRepository()
        _ = try await source.setMaximumLeitnerLevel(3, operationID: "shared-limit")
        let pending = try await source.pendingRecords()
        let outbound = try XCTUnwrap(pending.first { $0.recordName == "ProgressOperation/shared-limit" })
        XCTAssertEqual(outbound.fields["schema_version"], .integer(2))
        var fields = outbound.fields
        fields["server_revision"] = .integer(1)
        let remote = try CloudKitMappedRecord(kind: outbound.kind, recordName: outbound.recordName, fields: fields)

        try await destination.handle(.fetched([remote]))
        let maximum = await destination.maximumLeitnerLevel()
        XCTAssertEqual(maximum, 3)
        let received = await destination.snapshot()
        XCTAssertEqual(received.schemaVersion, 2)
    }

    func testRemoteMergeEmitsOnlyEffectiveProgressSnapshots() async throws {
        let (repository, _, _) = try makeRepository()
        let stream = await repository.progressSnapshots()
        var iterator = stream.makeAsyncIterator()
        let operation = ProgressOperation(
            operationID: "remote-operation",
            createdAt: Date(timeIntervalSince1970: 1_000),
            status: .applied,
            session: session("remote-session")
        )
        let base = try CloudKitMapping.operationRecord(operation)
        var fields = base.fields
        fields["server_revision"] = .integer(1)
        let remoteRecord = try CloudKitMappedRecord(
            kind: base.kind,
            recordName: base.recordName,
            fields: fields
        )

        try await repository.handle(.fetched([remoteRecord]))
        let first = await iterator.next()
        XCTAssertEqual(first?.aggregate.answered, 1)

        try await repository.handle(.fetched([remoteRecord]))
        let duplicateWaiter = Task { await iterator.next() }
        try await Task.sleep(nanoseconds: 50_000_000)
        duplicateWaiter.cancel()
        let duplicate = await duplicateWaiter.value
        XCTAssertNil(duplicate)
    }

    func testFullFetchMergesAuthoritativeSnapshotOverStaleLocalProgress() async throws {
        let acknowledgedOne = ProgressOperation(
            operationID: "acknowledged-1",
            createdAt: Date(timeIntervalSince1970: 1_001),
            status: .applied,
            session: session("local-session-1"),
            serverRevision: 1
        )
        let acknowledgedTwo = ProgressOperation(
            operationID: "acknowledged-2",
            createdAt: Date(timeIntervalSince1970: 1_002),
            status: .applied,
            session: session("local-session-2"),
            serverRevision: 2
        )
        let unsent = ProgressOperation(
            operationID: "unsent-5",
            createdAt: Date(timeIntervalSince1970: 1_003),
            status: .pending,
            session: session("local-session-3")
        )
        let localEnvelope = ProgressEnvelope(
            documentRevision: 2,
            actorID: "device-a",
            operationID: acknowledgedTwo.id,
            sessionDetails: [acknowledgedOne.session, acknowledgedTwo.session, unsent.session].compactMap { $0 },
            aggregate: AggregateSnapshot(sessionsTotal: 3, answered: 3, correct: 3),
            operations: [acknowledgedOne, acknowledgedTwo, unsent]
        )
        let store = CloudProgressMemoryStore(checkpoint: CloudProgressCheckpoint(
            envelope: localEnvelope,
            snapshotChangeTag: "stale-tag-7",
            sentOperationIDs: [acknowledgedOne.id, acknowledgedTwo.id]
        ))
        let transport = FakeTransport()
        let remoteThree = ProgressOperation(
            operationID: "remote-3",
            createdAt: Date(timeIntervalSince1970: 1_004),
            status: .applied,
            session: session("remote-session-3"),
            serverRevision: 3
        )
        let remoteFour = ProgressOperation(
            operationID: "remote-4",
            createdAt: Date(timeIntervalSince1970: 1_005),
            status: .applied,
            session: session("remote-session-4"),
            serverRevision: 4
        )
        let authoritativeEnvelope = ProgressEnvelope(
            documentRevision: 4,
            actorID: "remote-device",
            operationID: remoteFour.id,
            sessionDetails: [
                acknowledgedOne.session,
                acknowledgedTwo.session,
                remoteThree.session,
                remoteFour.session
            ].compactMap { $0 },
            aggregate: AggregateSnapshot(sessionsTotal: 4, answered: 4, correct: 4),
            operations: [acknowledgedOne, acknowledgedTwo, remoteThree, remoteFour]
        )
        transport.fetchResult = CloudProgressFetchResult(
            records: [try CloudKitMapping.snapshotRecord(authoritativeEnvelope)],
            snapshotChangeTag: "authoritative-tag-9"
        )
        let (repository, _, _) = try makeRepository(transport: transport, store: store)

        let result = try await repository.fetch(full: true)
        let merged = await repository.snapshot()

        XCTAssertTrue(result.isFullSnapshot)
        XCTAssertEqual(result.snapshotChangeTag, "authoritative-tag-9")
        XCTAssertEqual(merged.aggregate, AggregateSnapshot(sessionsTotal: 5, answered: 5, correct: 5))
        XCTAssertEqual(merged.operations.first(where: { $0.id == unsent.id })?.serverRevision, nil)
        let checkpoint = await repository.checkpointSnapshot()
        XCTAssertEqual(checkpoint.snapshotChangeTag, "authoritative-tag-9")
    }

    func testStateUpdateAndLocalProgressShareOneAtomicCheckpoint() async throws {
        let (repository, _, store) = try makeRepository()
        _ = try await repository.save(session())
        let engineState = Data([1, 2, 3, 4])
        try await repository.handle(.stateUpdate(engineState))

        let checkpoint = try XCTUnwrap(try store.load())
        XCTAssertEqual(checkpoint.engineState, engineState)
        XCTAssertEqual(checkpoint.envelope.aggregate.sessionsTotal, 1)
        XCTAssertEqual(checkpoint.envelope.sessionDetails.count, 1)
    }

    func testFetchAndSendAreExplicitAndAcknowledgeMappedRecords() async throws {
        let transport = FakeTransport()
        let (repository, _, store) = try makeRepository(transport: transport)
        _ = try await repository.save(session())

        let pending = try await repository.pendingRecords()
        XCTAssertTrue(pending.contains { $0.recordName == "ProgressSnapshot/current" })
        XCTAssertEqual(transport.fetchCount, 0)
        XCTAssertEqual(transport.sendCount, 0)

        // The pre-reservation local operation is intentionally not a server
        // record yet; an operation fetched without an authoritative revision
        // must fail closed. Exercise the explicit fetch path with no changes.
        transport.fetchResult = CloudProgressFetchResult()
        _ = try await repository.fetch()
        XCTAssertEqual(transport.fetchCount, 1)

        transport.sendResult = CloudProgressSendResult()
        _ = try await repository.send()
        XCTAssertEqual(transport.sendCount, 1)
        let afterSend = try await repository.pendingRecords()
        XCTAssertTrue(afterSend.isEmpty)
        XCTAssertEqual(try store.load()?.sentOperationIDs.count, 1)
    }

    func testProgressOperationAndSnapshotUseOneAtomicTransportCommit() async throws {
        let transport = FakeTransport()
        let (repository, _, _) = try makeRepository(transport: transport)
        _ = try await repository.save(session(), operationID: "atomic-operation")

        _ = try await repository.send()

        XCTAssertEqual(transport.atomicSendCount, 1)
        XCTAssertTrue(transport.zoneExists)
        XCTAssertTrue(transport.sendRecordNames.first?.contains("ProgressSnapshot/current") ?? false)
        XCTAssertTrue(transport.sendRecordNames.first?.contains("ProgressOperation/atomic-operation") ?? false)
    }

    func testReviewEventsAreAtomicAndFailureLeavesOperationPending() async throws {
        let transport = FakeTransport()
        transport.failAtomicBeforeCommit = true
        let (repository, _, store) = try makeRepository(transport: transport)
        _ = try await repository.save(timedSession("atomic"), operationID: "atomic-review")

        do {
            _ = try await repository.send()
            XCTFail("atomic failure must not acknowledge progress")
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .transportUnavailable)
        }
        XCTAssertTrue(transport.durableEventRecords.isEmpty)
        XCTAssertTrue(try XCTUnwrap(store.load()).sentOperationIDs.isEmpty)
        transport.failAtomicBeforeCommit = false
        _ = try await repository.send()
        XCTAssertEqual(transport.durableEventRecords.map(\.recordName), ["QuestionReviewEvent/atomic-review:0"])
        XCTAssertEqual(transport.sendRecordNames.last?.count, 3)
    }

    func testLostAcknowledgementRetriesIdenticalEventWithoutDuplicate() async throws {
        let transport = FakeTransport()
        transport.loseNextAtomicAcknowledgement = true
        let (repository, _, store) = try makeRepository(transport: transport)
        _ = try await repository.save(timedSession("lost"), operationID: "lost-review")
        do { _ = try await repository.send() } catch { }
        XCTAssertTrue(try XCTUnwrap(store.load()).sentOperationIDs.isEmpty)
        XCTAssertEqual(transport.durableEventRecords.count, 1)

        _ = try await repository.send()
        XCTAssertEqual(transport.durableEventRecords.count, 1)
        XCTAssertEqual(try XCTUnwrap(store.load()).sentOperationIDs, ["lost-review"])
    }

    func testConflictingOrPartialEventReplayCannotAcknowledge() async throws {
        for conflict in [false, true] {
            let transport = FakeTransport()
            transport.loseNextAtomicAcknowledgement = true
            let (repository, _, store) = try makeRepository(transport: transport)
            _ = try await repository.save(timedSession("replay"), operationID: "replay-review")
            do { _ = try await repository.send() } catch { }
            let name = "QuestionReviewEvent/replay-review:0"
            if conflict {
                var changed = try XCTUnwrap(transport.durableEventRecords.first).fields
                changed["outcome"] = .string("missed")
                transport.replaceEvent(try CloudKitMappedRecord(kind: .reviewEvent, recordName: name, fields: changed))
            } else {
                transport.removeEvent(name)
            }
            do {
                _ = try await repository.send()
                XCTFail("missing or conflicting event must fail")
            } catch let error as CloudProgressRepositoryError {
                XCTAssertEqual(error, .rebaseRequired)
            }
            XCTAssertTrue(try XCTUnwrap(store.load()).sentOperationIDs.isEmpty)
        }
    }

    func testMultiAnswerOperationPublishesOnlyCapturedTimestamps() async throws {
        let transport = FakeTransport()
        let (repository, _, store) = try makeRepository(transport: transport)
        let answers = [
            SessionAnswer(courseID: "course", packID: "pack", questionID: "q-1", correct: true,
                          answeredAt: Date(timeIntervalSince1970: 1_500)),
            SessionAnswer(courseID: "course", packID: "pack", questionID: "q-1", correct: false,
                          answeredAt: Date(timeIntervalSince1970: 1_600)),
            SessionAnswer(courseID: "course", packID: "pack", questionID: "q-2", correct: true)
        ]
        _ = try await repository.save(timedSession("multi", answers: answers), operationID: "multi-review")
        _ = try await repository.send()

        let events = try transport.durableEventRecords.map(CloudKitMapping.reviewEvent(from:))
        XCTAssertEqual(events.map(\.id), ["multi-review:0", "multi-review:1"])
        XCTAssertEqual(events.map(\.priorLevel), [1, 2])
        XCTAssertEqual(events.map(\.resultingLevel), [2, 1])
        XCTAssertEqual(transport.sendRecordNames.first?.count, 4)
        XCTAssertFalse(try XCTUnwrap(store.load()).remoteRecords.contains { $0.kind == .reviewEvent })
    }

    func testMaximumChangePublishesOnlyAffectedQuestions() async throws {
        let high = QuestionIdentity(courseID: "course", packID: "pack", questionID: "high")
        let low = QuestionIdentity(courseID: "course", packID: "pack", questionID: "low")
        let baseline = ProgressEnvelope(
            schemaVersion: 2, actorID: "device-a",
            srs: [
                SRSSnapshot(identity: high, state: try SRSState(
                    tier: 5, nextDueAt: Date(timeIntervalSince1970: 9_000),
                    lastReviewedAt: Date(timeIntervalSince1970: 1_000), intervalDays: 30, reviewCount: 2
                )),
                SRSSnapshot(identity: low, state: try SRSState(
                    tier: 2, nextDueAt: Date(timeIntervalSince1970: 8_000),
                    lastReviewedAt: Date(timeIntervalSince1970: 1_000), intervalDays: 3, reviewCount: 1
                ))
            ]
        )
        let transport = FakeTransport()
        try transport.seedAuthoritativeSnapshot(baseline)
        let (repository, _, _) = try makeRepository(
            transport: transport,
            store: CloudProgressMemoryStore(checkpoint: CloudProgressCheckpoint(envelope: baseline))
        )
        _ = try await repository.setMaximumLeitnerLevel(3, operationID: "cap-3")
        _ = try await repository.send()

        let event = try CloudKitMapping.reviewEvent(from: XCTUnwrap(transport.durableEventRecords.first))
        XCTAssertEqual(transport.durableEventRecords.count, 1)
        XCTAssertEqual(event.identity, high)
        XCTAssertEqual(event.outcome, .maximumLevelChanged)
        XCTAssertEqual(event.priorLevel, 5)
        XCTAssertEqual(event.resultingLevel, 3)
    }

    func testCapReplayRejectsMissingLastEvent() async throws {
        let transport = FakeTransport()
        let (repository, _, store) = try makeRepository(transport: transport)
        let answers = ["a", "b"].map { id in
            SessionAnswer(courseID: "course", packID: "pack", questionID: id,
                          correct: true, answeredAt: Date(timeIntervalSince1970: 1_500))
        }
        _ = try await repository.save(timedSession("seed-cap", answers: answers), operationID: "seed-cap")
        _ = try await repository.send()
        _ = try await repository.setMaximumLeitnerLevel(1, operationID: "cap-one")
        transport.loseNextAtomicAcknowledgement = true
        do { _ = try await repository.send() } catch { }
        XCTAssertEqual(transport.durableEventRecords.filter {
            $0.recordName.hasPrefix("QuestionReviewEvent/cap-one:")
        }.count, 2)
        transport.removeEvent("QuestionReviewEvent/cap-one:1")

        do {
            _ = try await repository.send()
            XCTFail("a missing final cap event must fail closed")
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .rebaseRequired)
        }
        XCTAssertFalse(try XCTUnwrap(store.load()).sentOperationIDs.contains("cap-one"))
    }

    func testLegacyCapMigrationRetriesAfterLostAcknowledgement() async throws {
        let identity = QuestionIdentity(courseID: "course", packID: "pack", questionID: "legacy")
        let legacy = ProgressEnvelope(actorID: "device-a", srs: [
            SRSSnapshot(identity: identity, state: try SRSState(
                tier: 7, nextDueAt: Date(timeIntervalSince1970: 1_000_000),
                lastReviewedAt: Date(timeIntervalSince1970: 1_000),
                intervalDays: 120, reviewCount: 6
            ))
        ])
        let transport = FakeTransport()
        try transport.seedAuthoritativeSnapshot(legacy)
        let store = CloudProgressMemoryStore(checkpoint: CloudProgressCheckpoint(envelope: legacy))
        let (repository, _, _) = try makeRepository(transport: transport, store: store)
        _ = try await repository.setMaximumLeitnerLevel(5, operationID: "migrate-legacy")
        transport.loseNextAtomicAcknowledgement = true
        do { _ = try await repository.send() } catch { }
        XCTAssertFalse(try XCTUnwrap(store.load()).sentOperationIDs.contains("migrate-legacy"))

        _ = try await repository.send()
        XCTAssertTrue(try XCTUnwrap(store.load()).sentOperationIDs.contains("migrate-legacy"))
        XCTAssertEqual(transport.durableEventRecords.map(\.recordName), ["QuestionReviewEvent/migrate-legacy:0"])
    }

    func testAcknowledgedCapRetryDoesNotPublishLaterUnsentReview() async throws {
        let transport = FakeTransport()
        let (source, _, store) = try makeRepository(transport: transport)
        _ = try await source.setMaximumLeitnerLevel(1, operationID: "cap-first")
        _ = try await source.send()
        var interrupted = try XCTUnwrap(store.load())
        interrupted.sentOperationIDs.remove("cap-first")
        interrupted.snapshotDirty = true
        try store.save(interrupted)
        let (resumed, _, _) = try makeRepository(transport: transport, store: store)
        _ = try await resumed.save(timedSession("later"), operationID: "review-later")
        transport.failAtomicOnCall = transport.atomicSendCount + 2

        do { _ = try await resumed.send() } catch { }
        let published = try CloudKitMapping.snapshot(from: XCTUnwrap(transport.lastPublishedSnapshotRecord))
        XCTAssertEqual(published.aggregate.answered, 0)
        XCTAssertFalse(published.operations.contains { $0.id == "review-later" })
        let saved = try XCTUnwrap(store.load())
        XCTAssertTrue(saved.sentOperationIDs.contains("cap-first"))
        XCTAssertFalse(saved.sentOperationIDs.contains("review-later"))
    }

    func testLostAcknowledgementRebasesWhenAnotherDeviceAdvancesServer() async throws {
        let transport = FakeTransport()
        let (firstDevice, _, firstStore) = try makeRepository(transport: transport)
        _ = try await firstDevice.save(timedSession("first"), operationID: "first-review")
        transport.loseNextAtomicAcknowledgement = true
        do { _ = try await firstDevice.send() } catch { }
        XCTAssertFalse(try XCTUnwrap(firstStore.load()).sentOperationIDs.contains("first-review"))

        let firstPublished = try CloudKitMapping.snapshot(from: XCTUnwrap(transport.lastPublishedSnapshotRecord))
        let secondStore = CloudProgressMemoryStore(checkpoint: CloudProgressCheckpoint(
            envelope: firstPublished,
            snapshotChangeTag: transport.authoritativeChangeTag,
            sentOperationIDs: ["first-review"]
        ))
        let (secondDevice, _, _) = try makeRepository(transport: transport, store: secondStore)
        _ = try await secondDevice.save(timedSession("second"), operationID: "second-review")
        _ = try await secondDevice.send()

        do {
            _ = try await firstDevice.send()
            XCTFail("a later server write requires a fetch and rebase")
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .rebaseRequired)
        }
        XCTAssertFalse(try XCTUnwrap(firstStore.load()).sentOperationIDs.contains("first-review"))
    }

    func testCapChangeOverAtomicRecordLimitLeavesLocalStateUntouched() async throws {
        let states = try (0..<249).map { index in
            SRSSnapshot(
                identity: QuestionIdentity(courseID: "course", packID: "pack", questionID: "q-\(index)"),
                state: try SRSState(tier: 2, nextDueAt: Date(timeIntervalSince1970: 1_000),
                                    lastReviewedAt: nil, intervalDays: 3, reviewCount: 0)
            )
        }
        let baseline = ProgressEnvelope(schemaVersion: 2, actorID: "device-a", srs: states)
        let (repository, _, _) = try makeRepository(store: CloudProgressMemoryStore(
            checkpoint: CloudProgressCheckpoint(envelope: baseline)
        ))
        do {
            _ = try await repository.setMaximumLeitnerLevel(1)
            XCTFail("a change requiring 249 events exceeds the atomic record limit")
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .maximumLevelChangeTooLarge(affected: 249, limit: 248))
        }
        let after = await repository.snapshot()
        XCTAssertEqual(after.maximumLeitnerLevel, 5)
        XCTAssertEqual(after.srs.count, 249)
    }

    func testMissingCapEventLeavesQuestionHistoryIncomplete() async throws {
        let transport = FakeTransport()
        let (repository, _, _) = try makeRepository(transport: transport)
        _ = try await repository.save(timedSession("review"), operationID: "review")
        _ = try await repository.send()
        _ = try await repository.setMaximumLeitnerLevel(1, operationID: "cap")
        _ = try await repository.send()
        transport.removeEvent("QuestionReviewEvent/cap:0")

        let history = try await repository.reviewHistory(for: QuestionIdentity(
            courseID: "course", packID: "pack", questionID: "q-1"
        ))
        XCTAssertFalse(history.isComplete)
    }

    func testSingleOperationBeyondRecordBudgetFailsClosed() async throws {
        let transport = FakeTransport()
        let (repository, _, store) = try makeRepository(transport: transport)
        let answers = (0..<249).map { index in
            SessionAnswer(courseID: "course", packID: "pack", questionID: "q-\(index)",
                          correct: true, answeredAt: Date(timeIntervalSince1970: Double(index + 1)))
        }
        _ = try await repository.save(timedSession("oversize", answers: answers), operationID: "oversize-review")
        do {
            _ = try await repository.send()
            XCTFail("snapshot, operation and 249 events exceed 250")
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .invalidOperation)
        }
        XCTAssertEqual(transport.atomicSendCount, 0)
        XCTAssertTrue(try XCTUnwrap(store.load()).sentOperationIDs.isEmpty)
    }

    func testExactly250RecordsFitAndAssignedOrderDeterminesEvents() async throws {
        let transport = FakeTransport()
        let (repository, _, _) = try makeRepository(transport: transport)
        let answers = (0..<248).map { index in
            SessionAnswer(courseID: "course", packID: "pack", questionID: "q-\(index)",
                          correct: true, answeredAt: Date(timeIntervalSince1970: Double(index + 1)))
        }
        _ = try await repository.save(timedSession("fits", answers: answers), operationID: "fits-review")
        _ = try await repository.send()
        XCTAssertEqual(transport.sendRecordNames.first?.count, 250)
        XCTAssertEqual(transport.durableEventRecords.count, 248)

        let orderTransport = FakeTransport()
        let (ordered, _, _) = try makeRepository(transport: orderTransport)
        _ = try await ordered.save(timedSession("later"), operationID: "z-operation")
        _ = try await ordered.save(timedSession("earlier"), operationID: "a-operation")
        _ = try await ordered.send()
        let events = try orderTransport.durableEventRecords.map(CloudKitMapping.reviewEvent(from:))
            .sorted { ($0.serverRevision ?? 0) < ($1.serverRevision ?? 0) }
        XCTAssertEqual(events.map(\.operationID), ["a-operation", "z-operation"])
        XCTAssertEqual(events.map(\.priorLevel), [1, 2])
        XCTAssertEqual(events.map(\.resultingLevel), [2, 3])
    }

    func testLaterAtomicBatchUsesOnlyPreviouslyPublishedProgress() async throws {
        let transport = FakeTransport()
        let (repository, _, _) = try makeRepository(transport: transport)
        for index in 0..<126 {
            let answer = SessionAnswer(
                courseID: "course", packID: "pack", questionID: "q-\(index)",
                correct: true, answeredAt: Date(timeIntervalSince1970: Double(index + 1))
            )
            _ = try await repository.save(
                timedSession("batch-\(index)", answers: [answer]),
                operationID: String(format: "batch-%03d", index)
            )
        }
        _ = try await repository.send()
        let last = try XCTUnwrap(transport.durableEventRecords.first {
            $0.recordName == "QuestionReviewEvent/batch-125:0"
        })
        XCTAssertEqual(try CloudKitMapping.reviewEvent(from: last).priorLevel, 1)
        XCTAssertTrue(transport.sendRecordNames.allSatisfy { $0.count <= 250 })
    }

    func testRepositoryDoesNotAcknowledgeMissingEventReceipt() async throws {
        let transport = FakeTransport()
        let (repository, _, store) = try makeRepository(transport: transport)
        _ = try await repository.save(timedSession("receipt"), operationID: "receipt-review")
        transport.sendResult = CloudProgressSendResult(savedRecordNames: [
            CloudKitContract.snapshotRecordName, "ProgressOperation/receipt-review"
        ])
        do {
            _ = try await repository.send()
            XCTFail("the event receipt is required")
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .rebaseRequired)
        }
        XCTAssertTrue(try XCTUnwrap(store.load()).sentOperationIDs.isEmpty)
    }

    func testCompetingWritersUseOptimisticRevisionAndOneMustRebase() async throws {
        let transport = SerializedRevisionTransport()
        let left = try CloudProgressRepository(
            actorID: "left-device",
            persistence: CloudProgressMemoryStore(),
            transport: transport
        )
        let right = try CloudProgressRepository(
            actorID: "right-device",
            persistence: CloudProgressMemoryStore(),
            transport: transport
        )
        _ = try await left.save(session("left"), operationID: "left-operation")
        _ = try await right.save(session("right"), operationID: "right-operation")

        func send(_ repository: CloudProgressRepository) async -> CloudProgressRepositoryError? {
            do {
                _ = try await repository.send()
                return nil
            } catch let error as CloudProgressRepositoryError {
                return error
            } catch {
                return .transportUnavailable
            }
        }

        async let leftError = send(left)
        async let rightError = send(right)
        let errors = await [leftError, rightError]
        XCTAssertEqual(errors.filter { $0 == nil }.count, 1)
        XCTAssertEqual(errors.filter { $0 == CloudProgressRepositoryError.rebaseRequired }.count, 1)
    }

    func testLargePendingSetUsesAtomicSub250ProgressBatches() async throws {
        let transport = FakeTransport()
        let (repository, _, store) = try makeRepository(transport: transport)
        for index in 0..<250 {
            _ = try await repository.save(
                session("batch-\(index)"),
                operationID: String(format: "batch-%03d", index)
            )
        }

        _ = try await repository.send()

        XCTAssertEqual(transport.atomicSendCount, 2)
        XCTAssertTrue(transport.sendRecordNames.allSatisfy { $0.contains(CloudKitContract.snapshotRecordName) })
        XCTAssertTrue(transport.sendRecordNames.allSatisfy { $0.count <= 250 })
        XCTAssertEqual(try store.load()?.snapshotChangeTag, "atomic-2")
        let pending = try await repository.pendingRecords()
        XCTAssertEqual(pending, [])
    }

    func testDeletedZoneRequiresEmptyFullFetchBeforeBootstrap() async throws {
        let transport = FakeTransport()
        let (repository, _, store) = try makeRepository(transport: transport)
        _ = try await repository.save(session("before-delete"), operationID: "before-delete")
        _ = try await repository.send()
        transport.deleteZone()
        _ = try await repository.save(session("after-delete"), operationID: "after-delete")

        do {
            _ = try await repository.send()
            XCTFail("a deleted zone must require a full empty-zone fetch")
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .rebaseRequired)
        }
        XCTAssertTrue(try XCTUnwrap(try store.load()).requiresRebase)

        _ = try await repository.fetch()

        let snapshot = await repository.snapshot()
        XCTAssertEqual(snapshot.documentRevision, 0)
        XCTAssertEqual(snapshot.compaction.watermarkRevision, 0)
        XCTAssertTrue(snapshot.operations.allSatisfy { $0.serverRevision == nil })

        _ = try await repository.send()
        XCTAssertTrue(transport.zoneExists)
        let republished = await repository.snapshot()
        XCTAssertEqual(republished.documentRevision, 2)
    }

    func testDeletedZoneReplaysRetainedAcknowledgedIssue() async throws {
        let transport = FakeTransport()
        let store = CloudProgressMemoryStore()
        let retainedIssue = try issue("retained-after-delete")
        try store.save(CloudProgressCheckpoint(
            envelope: ProgressEnvelope(actorID: "device-a", issues: [retainedIssue]),
            sentIssueIDs: [retainedIssue.issueID],
            snapshotDirty: true,
            requiresRebase: true
        ))
        let repository = try CloudProgressRepository(
            actorID: "device-a",
            persistence: store,
            transport: transport
        )

        _ = try await repository.fetch()

        let rebased = try XCTUnwrap(try store.load())
        XCTAssertEqual(rebased.envelope.issues.map(\.issueID), [retainedIssue.issueID])
        XCTAssertTrue(rebased.sentIssueIDs.isEmpty)

        _ = try await repository.send()

        XCTAssertTrue(
            transport.sendRecordNames.flatMap { $0 }.contains(
                "\(CloudKitRecordKind.issue.rawValue)/\(retainedIssue.issueID)"
            )
        )
    }

    func testRemoteFullIssueQueueDefersLocalIssueWithoutRebaseLatch() async throws {
        let transport = FakeTransport()
        let store = CloudProgressMemoryStore()
        let localIssue = try issue("local-overflow")
        try store.save(CloudProgressCheckpoint(
            envelope: ProgressEnvelope(actorID: "device-a", issues: [localIssue]),
            snapshotDirty: true,
            requiresRebase: true
        ))
        let repository = try CloudProgressRepository(
            actorID: "device-a",
            persistence: store,
            transport: transport
        )
        let remoteIssues = try (0..<CloudKitContract.maximumQueuedIssues).map {
            try issue("remote-overflow-\($0)")
        }
        let remoteSnapshot = try CloudKitMapping.snapshotRecord(
            ProgressEnvelope(actorID: "device-b", issues: remoteIssues)
        )
        transport.fetchResult = CloudProgressFetchResult(
            records: [remoteSnapshot],
            snapshotChangeTag: "remote-tag",
            isFullSnapshot: true
        )

        _ = try await repository.fetch(full: true)
        let checkpoint = try XCTUnwrap(try store.load())
        XCTAssertFalse(checkpoint.requiresRebase)
        XCTAssertEqual(checkpoint.envelope.issues.map(\.issueID), ["local-overflow"])
    }

    func testIssueCASIsIdempotentForIdenticalReplayAndRejectsChangedPayload() async throws {
        let transport = FakeTransport()
        let first = try issue("replay-issue")
        let firstRecord = try CloudKitMapping.issueRecord(first)
        _ = try await transport.sendIssuesAtomically([firstRecord])
        let replay = try await transport.sendIssuesAtomically([firstRecord])
        XCTAssertEqual(replay.savedRecordNames, [firstRecord.recordName])

        let changed = try QuestionIssue(
            issueID: first.issueID,
            courseID: first.courseID,
            packID: first.packID,
            questionID: first.questionID,
            questionType: first.questionType,
            appVersion: first.appVersion,
            build: first.build,
            description: "changed payload"
        )
        let changedRecord = try CloudKitMapping.issueRecord(changed)
        do {
            _ = try await transport.sendIssuesAtomically([changedRecord])
            XCTFail("changed same-ID issue payload must conflict")
        } catch let error as CloudProgressTransportError {
            XCTAssertEqual(error, .serverRecordChanged)
        }
    }

    func testDurableMergeRecoveryUsesTransportForCompactionDeletes() async throws {
        let transport = FakeTransport()
        transport.seedAuthoritativeRevision(1)
        let store = CloudProgressMemoryStore()
        let now = Date()
        let old = ProgressMergeOperation(
            operationID: "old",
            baseRevision: 0,
            serverRevision: 1,
            createdAt: now.addingTimeInterval(-40 * 86_400),
            updatedAt: now.addingTimeInterval(-40 * 86_400),
            serverRecordedAt: now.addingTimeInterval(-40 * 86_400),
            session: session("old")
        )
        let merged = try ProgressMergeEngine.merge(
            [old],
            into: .empty(actorID: "device-a", createdAt: now),
            now: now
        )
        let checkpoint = CloudProgressCheckpoint(
            envelope: merged.snapshot.envelope,
            sentOperationIDs: [old.operationID],
            mergeSnapshot: merged.snapshot
        )
        try store.save(checkpoint)
        let repository = try CloudProgressRepository(
            actorID: "device-a",
            persistence: store,
            transport: transport
        )

        _ = try await repository.send()

        XCTAssertEqual(transport.deleteRecordNames, [["ProgressOperation/old"]])
        XCTAssertTrue(transport.sendRecordNames.flatMap { $0 }.contains(CloudKitContract.snapshotRecordName))
        let persisted = try XCTUnwrap(try store.load())
        XCTAssertTrue(persisted.pendingCompactionDeleteIDs.isEmpty)
        XCTAssertNil(persisted.recoveryCheckpoint)
        XCTAssertFalse(persisted.mergeSnapshot?.operations.contains(where: { $0.operationID == "old" }) ?? true)
        XCTAssertFalse(persisted.envelope.operations.contains(where: { $0.id == "old" }))
    }

    func testRepositoryRecoveryResumesAfterEachDurableStageWriteFailure() async throws {
        let now = Date()
        let old = ProgressMergeOperation(
            operationID: "old",
            baseRevision: 0,
            serverRevision: 1,
            createdAt: now.addingTimeInterval(-40 * 86_400),
            updatedAt: now.addingTimeInterval(-40 * 86_400),
            serverRecordedAt: now.addingTimeInterval(-40 * 86_400),
            session: session("old")
        )
        let merged = try ProgressMergeEngine.merge(
            [old],
            into: .empty(actorID: "device-a", createdAt: now),
            now: now
        ).snapshot
        let initial = CloudProgressCheckpoint(
            envelope: merged.envelope,
            sentOperationIDs: [old.operationID],
            mergeSnapshot: merged
        )

        var finalHashes = Set<String>()
        for failureNumber in 1...6 {
            let store = FailingCheckpointStore(initial: initial, failOnSave: failureNumber)
            let transport = FakeTransport()
            transport.seedAuthoritativeRevision(1)
            let repository = try CloudProgressRepository(
                actorID: "device-a",
                persistence: store,
                transport: transport
            )

            do {
                _ = try await repository.send()
                XCTFail("injected checkpoint failure must interrupt send")
            } catch {
                // The first failed durable stage is intentionally surfaced.
            }
            let resumed = try CloudProgressRepository(
                actorID: "device-a",
                persistence: store,
                transport: transport
            )
            _ = try await resumed.send()
            let checkpoint = try XCTUnwrap(try store.load())
            XCTAssertNil(checkpoint.recoveryCheckpoint)
            XCTAssertTrue(checkpoint.pendingCompactionDeleteIDs.isEmpty)
            XCTAssertFalse(checkpoint.envelope.operations.contains { $0.id == "old" })
            finalHashes.insert(try XCTUnwrap(checkpoint.mergeSnapshot).canonicalEvidenceHash())
        }
        XCTAssertEqual(finalHashes.count, 1)
    }

    func testReachabilityAndAccountChangeAreFailVisibleAndResetOnlyEngineState() async throws {
        let transport = FakeTransport()
        let (repository, _, store) = try makeRepository(transport: transport)
        _ = try await repository.save(session())
        try await repository.handle(.stateUpdate(Data([9])))
        try await repository.handle(.reachability(false))

        do {
            _ = try await repository.send()
            XCTFail("offline send must fail")
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .offline)
        }
        XCTAssertEqual(transport.sendCount, 0)

        try await repository.handle(.accountChanged)
        let checkpoint = try XCTUnwrap(try store.load())
        XCTAssertNil(checkpoint.engineState)
        XCTAssertEqual(transport.resetCount, 1)
        XCTAssertEqual(checkpoint.envelope.aggregate.sessionsTotal, 1)
        let statuses = await repository.statusHistory()
        XCTAssertTrue(statuses.contains { $0.state == .accountIsolationRequired && $0.reason == .accountChanged })
    }

    func testPerRecordRetryUsesDeterministicBackoffAndRedactedStatus() async throws {
        let transport = FakeTransport()
        let (repository, _, _) = try makeRepository(transport: transport)
        _ = try await repository.save(session())
        transport.sendResult = CloudProgressSendResult(failedRecords: [
            .init(recordName: "ProgressOperation/secret-id", reason: .network, retryable: true)
        ])

        do {
            _ = try await repository.send()
            XCTFail("partial failure must throw")
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .partialFailure)
        }

        let history = await repository.statusHistory()
        let event = try XCTUnwrap(history.last { $0.state == .retryScheduled })
        XCTAssertEqual(event.retryAfterMilliseconds, 10)
        XCTAssertFalse(event.redactedPayload.values.contains { $0.contains("secret-id") })
        XCTAssertFalse(event.redactedPayload.values.contains { $0.contains("question") })
    }

    func testTokenExpiryRequiresRecoveryAndDoesNotFetchFromEventHandler() async throws {
        let transport = FakeTransport()
        let (repository, _, store) = try makeRepository(transport: transport)
        try await repository.handle(.stateUpdate(Data([7])))
        try await repository.handle(.tokenExpired)
        XCTAssertEqual(transport.fetchCount, 0)
        XCTAssertEqual(transport.sendCount, 0)
        XCTAssertEqual(transport.resetCount, 1)
        XCTAssertNil(try store.load()?.engineState)
        let history = await repository.statusHistory()
        XCTAssertTrue(history.contains { $0.reason == .tokenExpired && $0.state == .rebasing })
    }

    func testIssueAcknowledgementRemovesOnlyAfterRecordSaveAndRequiresSnapshotRefresh() async throws {
        let transport = FakeTransport()
        let (repository, _, store) = try makeRepository(transport: transport)
        _ = try await repository.queueIssue(issue())

        let firstBatch = try await repository.pendingRecords()
        transport.sendResult = CloudProgressSendResult(savedRecordNames: firstBatch.map(\.recordName))
        _ = try await repository.send()

        let afterIssueAck = await repository.checkpointSnapshot()
        XCTAssertTrue(afterIssueAck.envelope.issues.isEmpty)
        XCTAssertFalse(afterIssueAck.snapshotDirty)
        let pendingAfterIssueAck = try await repository.pendingRecords()
        XCTAssertTrue(pendingAfterIssueAck.isEmpty)
        let history = await repository.statusHistory()
        XCTAssertTrue(history.contains { $0.state == .synced && $0.reason == .completed })

        let reloaded = try CloudProgressRepository(
            actorID: "device-a",
            persistence: store,
            transport: transport
        )
        let reloadedCheckpoint = await reloaded.checkpointSnapshot()
        XCTAssertEqual(reloadedCheckpoint.envelope, afterIssueAck.envelope)
        XCTAssertFalse(reloadedCheckpoint.snapshotDirty)
    }

    func testTerminalRecordFailureRetainsWorkAndNeverReportsCompletion() async throws {
        let transport = FakeTransport()
        let (repository, _, store) = try makeRepository(transport: transport)
        let operation = try await repository.save(session(), operationID: "operation-failure")
        let operationRecord = try CloudKitMapping.operationRecord(operation)
        transport.sendResult = CloudProgressSendResult(failedRecords: [
            .init(recordName: operationRecord.recordName, reason: .permissionDenied, retryable: false)
        ])

        do {
            _ = try await repository.send()
            XCTFail("terminal record failure must throw")
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .partialFailure)
        }

        let checkpoint = await repository.checkpointSnapshot()
        XCTAssertEqual(checkpoint.envelope.operations.first?.status, .failed)
        XCTAssertEqual(checkpoint.envelope.operations.first?.error, .failed("cloud_sync_failed"))
        XCTAssertTrue(checkpoint.snapshotDirty)
        let history = await repository.statusHistory()
        XCTAssertFalse(history.contains { $0.state == .synced && $0.reason == .completed })

        let sendCountAfterFailure = transport.sendCount
        transport.sendResult = CloudProgressSendResult()
        do {
            _ = try await repository.send()
            XCTFail("terminal operation failure requires manual resolution")
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .partialFailure)
        }
        XCTAssertEqual(transport.sendCount, sendCountAfterFailure)
        let afterRetryHistory = await repository.statusHistory()
        XCTAssertFalse(afterRetryHistory.contains { $0.state == .synced && $0.reason == .completed })

        let reloaded = try CloudProgressRepository(
            actorID: "device-a",
            persistence: store,
            transport: transport
        )
        let reloadedCheckpoint = await reloaded.checkpointSnapshot()
        XCTAssertEqual(reloadedCheckpoint.envelope, checkpoint.envelope)
    }

    func testNilSessionIntentRemainsDurableWithoutBlockingSharedLogSend() async throws {
        let transport = FakeTransport()
        let (repository, _, store) = try makeRepository(transport: transport)
        _ = try await repository.enqueue(ProgressOperation(
            operationID: "nil-session",
            status: .pending,
            session: nil
        ))

        _ = try await repository.send()
        let checkpoint = try XCTUnwrap(try store.load())
        XCTAssertEqual(checkpoint.envelope.operations.map(\.id), ["nil-session"])
        XCTAssertTrue(checkpoint.sentOperationIDs.isEmpty)
        XCTAssertFalse(checkpoint.snapshotDirty)
    }

    func testTerminalIssueFailureRetainsRedactedReasonAndNoRemoteIdentifier() async throws {
        let transport = FakeTransport()
        let (repository, _, _) = try makeRepository(transport: transport)
        _ = try await repository.queueIssue(issue("issue-terminal"))
        let pendingRecords = try await repository.pendingRecords()
        let issueRecord = try XCTUnwrap(pendingRecords.first { $0.kind == .issue })
        transport.sendResult = CloudProgressSendResult(failedRecords: [
            .init(recordName: issueRecord.recordName, reason: .permissionDenied, retryable: false)
        ])

        do { _ = try await repository.send(); XCTFail("terminal issue failure must throw") } catch { }
        let checkpoint = await repository.checkpointSnapshot()
        XCTAssertEqual(checkpoint.failedIssueReasons["issue-terminal"], .permissionDenied)
        XCTAssertEqual(checkpoint.envelope.issues.first?.issueID, "issue-terminal")
        let history = await repository.statusHistory()
        XCTAssertFalse(history.last?.redactedPayload.values.contains { $0.contains("issue-terminal") } ?? false)

        let sendCountAfterFailure = transport.sendCount
        transport.sendResult = CloudProgressSendResult()
        _ = try await repository.send()
        XCTAssertEqual(transport.sendCount, sendCountAfterFailure + 1)
        let afterRetryHistory = await repository.statusHistory()
        XCTAssertTrue(afterRetryHistory.contains { $0.state == .synced && $0.reason == .completed })
    }

    func testTokenRecoveryReDerivesSnapshotFromDurableEnvelope() async throws {
        let transport = FakeTransport()
        let (repository, _, _) = try makeRepository(transport: transport)
        _ = try await repository.save(session())
        transport.sendResult = CloudProgressSendResult()
        _ = try await repository.send()
        try await repository.handle(.tokenExpired)
        let pending = try await repository.pendingRecords()
        XCTAssertEqual(pending.map(\.recordName), [CloudKitContract.snapshotRecordName])
        let snapshot = await repository.snapshot()
        XCTAssertEqual(snapshot.aggregate.sessionsTotal, 1)
    }

    func testFetchPreservesRemoteCacheWhenThereAreNoChanges() async throws {
        let transport = FakeTransport()
        let (repository, _, _) = try makeRepository(transport: transport)
        let operation = ProgressOperation(
            operationID: "remote-operation",
            createdAt: Date(timeIntervalSince1970: 1),
            status: .applied,
            session: session("remote-session"),
            serverRevision: 1
        )
        let remoteRecord = try CloudKitMapping.operationRecord(operation)
        try await repository.handle(.fetched([remoteRecord]))

        transport.fetchResult = CloudProgressFetchResult()
        _ = try await repository.fetch()

        let checkpoint = await repository.checkpointSnapshot()
        XCTAssertEqual(checkpoint.remoteRecords, [remoteRecord])
    }

    func testFetchedOperationsReduceThroughDurableMergeCheckpoint() async throws {
        let transport = FakeTransport()
        let (repository, _, store) = try makeRepository(transport: transport)
        let operation = ProgressOperation(
            operationID: "remote-operation",
            createdAt: Date(timeIntervalSince1970: 1),
            status: .applied,
            session: session("remote-session")
        )
        let mapped = try CloudKitMapping.operationRecord(operation)
        var remoteFields = mapped.fields
        remoteFields["server_revision"] = .integer(42)
        let remoteRecord = try CloudKitMappedRecord(
            kind: mapped.kind,
            recordName: mapped.recordName,
            fields: remoteFields
        )

        try await repository.handle(.fetched([remoteRecord]))

        let checkpoint = try XCTUnwrap(try store.load())
        XCTAssertEqual(checkpoint.envelope.aggregate.sessionsTotal, 1)
        XCTAssertEqual(checkpoint.mergeSnapshot?.operations.map(\.operationID), ["remote-operation"])
        XCTAssertEqual(checkpoint.mergeSnapshot?.operations.first?.serverRevision, 42)
        XCTAssertEqual(checkpoint.remoteRecords, [remoteRecord])
    }

    func testCrossDeviceSnapshotRebasePreservesLocalIssueAndFailureState() async throws {
        let transport = FakeTransport()
        let store = CloudProgressMemoryStore()
        let localIssue = try issue("local-issue")
        let remoteIssue = try issue("remote-issue")
        try store.save(CloudProgressCheckpoint(
            envelope: ProgressEnvelope(actorID: "device-a", issues: [localIssue]),
            snapshotDirty: true,
            requiresRebase: true,
            failedIssueReasons: [localIssue.issueID: .network]
        ))
        let repository = try CloudProgressRepository(
            actorID: "device-a",
            persistence: store,
            transport: transport
        )
        let remoteSnapshot = try CloudKitMapping.snapshotRecord(
            ProgressEnvelope(actorID: "device-b", issues: [remoteIssue])
        )

        try await repository.handle(.fetched([remoteSnapshot]))

        let checkpoint = try XCTUnwrap(try store.load())
        XCTAssertEqual(checkpoint.envelope.actorID, "device-a")
        XCTAssertEqual(checkpoint.envelope.issues.map(\.issueID), ["local-issue"])
        XCTAssertEqual(checkpoint.failedIssueReasons, ["local-issue": .network])
    }

    func testFetchedForeignOperationIsServerKnownAndLocalOperationPublishes() async throws {
        let transport = FakeTransport()
        let (repository, _, store) = try makeRepository(transport: transport)
        let foreign = ProgressOperation(
            operationID: "foreign-operation",
            createdAt: Date(timeIntervalSince1970: 1),
            status: .applied,
            session: session("foreign-session")
        )
        let foreignBase = try CloudKitMapping.operationRecord(foreign)
        var foreignFields = foreignBase.fields
        foreignFields["server_revision"] = .integer(1)
        let foreignRecord = try CloudKitMappedRecord(
            kind: .operation,
            recordName: foreignBase.recordName,
            fields: foreignFields
        )
        transport.seedExistingOperation(foreignRecord)
        try await repository.handle(.fetched([foreignRecord]))
        _ = try await repository.save(session("local-session"), operationID: "local-operation")

        _ = try await repository.send()

        let checkpoint = try XCTUnwrap(try store.load())
        XCTAssertTrue(checkpoint.sentOperationIDs.contains("foreign-operation"))
        XCTAssertTrue(checkpoint.sentOperationIDs.contains("local-operation"))
        XCTAssertFalse(transport.sendRecordNames.flatMap { $0 }.contains("ProgressOperation/foreign-operation"))
        XCTAssertEqual(checkpoint.envelope.documentRevision, 2)
    }

    func testLateFetchedRevisionSetsDurableRebaseGate() async throws {
        let transport = FakeTransport()
        let (repository, _, store) = try makeRepository(transport: transport)

        func record(_ id: String, revision: Int) throws -> CloudKitMappedRecord {
            let operation = ProgressOperation(
                operationID: id,
                createdAt: Date(timeIntervalSince1970: TimeInterval(revision)),
                status: .applied,
                session: session("session-\(id)")
            )
            let mapped = try CloudKitMapping.operationRecord(operation)
            var fields = mapped.fields
            fields["server_revision"] = .integer(Int64(revision))
            return try CloudKitMappedRecord(kind: mapped.kind, recordName: mapped.recordName, fields: fields)
        }

        try await repository.handle(.fetched([try record("newer", revision: 5)]))
        do {
            try await repository.handle(.fetched([try record("late", revision: 3)]))
            XCTFail("late revisions must require a full rebase")
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .rebaseRequired)
        }
        XCTAssertTrue(try XCTUnwrap(try store.load()).requiresRebase)
    }

    func testRecoveryPersistsRebaseGateAndFullSnapshotFetchClearsItBeforeSend() async throws {
        let transport = FakeTransport()
        let (repository, _, store) = try makeRepository(transport: transport)
        _ = try await repository.save(session())
        try await repository.handle(.tokenExpired)

        XCTAssertTrue(try XCTUnwrap(try store.load()).requiresRebase)
        do {
            _ = try await repository.send()
            XCTFail("send must wait for a successful rebase fetch")
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .rebaseRequired)
        }
        XCTAssertEqual(transport.sendCount, 0)
        let recoveryHistory = await repository.statusHistory()
        XCTAssertEqual(recoveryHistory.last?.state, .rebasing)

        _ = try await repository.fetch()
        XCTAssertFalse(try XCTUnwrap(try store.load()).requiresRebase)
        let fullSnapshot = try CloudKitMapping.snapshotRecord(await repository.snapshot())
        transport.fetchResult = CloudProgressFetchResult(records: [fullSnapshot])
        _ = try await repository.fetch()
        XCTAssertFalse(try XCTUnwrap(try store.load()).requiresRebase)
        _ = try await repository.send()
        XCTAssertEqual(transport.sendCount, 1)
    }

    func testFetchedSnapshotChangeTagIsRetainedForOptimisticPublish() async throws {
        let transport = FakeTransport()
        let (repository, _, store) = try makeRepository(transport: transport)
        let record = try CloudKitMapping.snapshotRecord(await repository.snapshot())
        transport.fetchResult = CloudProgressFetchResult(
            records: [record],
            snapshotChangeTag: "change-tag-1"
        )

        _ = try await repository.fetch()

        XCTAssertEqual(try store.load()?.snapshotChangeTag, "change-tag-1")
    }

    func testOversizedSnapshotSendIsTypedTerminalFailure() async throws {
        let transport = FakeTransport()
        let (repository, _, _) = try makeRepository(transport: transport)
        let oversizedAnswer = SessionAnswer(
            courseID: String(repeating: "c", count: 300_000),
            packID: String(repeating: "p", count: 300_000),
            questionID: String(repeating: "q", count: 300_000),
            correct: true
        )
        _ = try await repository.save(SessionDetail(
            sessionID: "large",
            completedAt: Date(timeIntervalSince1970: 1_000),
            answers: [oversizedAnswer]
        ))

        do {
            _ = try await repository.send()
            XCTFail("oversized snapshot must fail")
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .encodedSizeRefused)
        }
        let history = await repository.statusHistory()
        XCTAssertEqual(history.last?.state, .failed)
        XCTAssertEqual(history.last?.reason, .encodedSizeRefused)
        XCTAssertEqual(transport.sendCount, 0)
    }

    func testRetryPolicyCountsOneAttemptForRetryableBatch() async throws {
        let transport = FakeTransport()
        let (repository, _, _) = try makeRepository(transport: transport)
        let operation = try await repository.save(session())
        let operationRecord = try CloudKitMapping.operationRecord(operation)
        let issueRecord = try CloudKitMapping.issueRecord(issue())
        transport.sendResult = CloudProgressSendResult(failedRecords: [
            .init(recordName: operationRecord.recordName, reason: .network, retryable: true),
            .init(recordName: issueRecord.recordName, reason: .network, retryable: true)
        ])

        do {
            _ = try await repository.send()
            XCTFail("retryable batch must throw for the caller to retry")
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .partialFailure)
        }

        let history = await repository.statusHistory()
        let retryEvent = try XCTUnwrap(history.last { $0.state == .retryScheduled })
        XCTAssertEqual(retryEvent.retryAttempt, 1)
    }

    func testSuccessfulFetchAndReachabilityResetRetryAttempt() async throws {
        let transport = FakeTransport()
        let (repository, _, _) = try makeRepository(transport: transport)
        let operation = try await repository.save(session())
        let operationRecord = try CloudKitMapping.operationRecord(operation)
        transport.sendResult = CloudProgressSendResult(failedRecords: [
            .init(recordName: operationRecord.recordName, reason: .network, retryable: true)
        ])

        _ = try? await repository.send()
        _ = try await repository.fetch()
        _ = try? await repository.send()
        try await repository.handle(.reachability(false))
        try await repository.handle(.reachability(true))
        _ = try? await repository.send()

        let retryEvents = (await repository.statusHistory()).filter { $0.state == .retryScheduled }
        XCTAssertEqual(retryEvents.map(\.retryAttempt), [1, 1, 1])
    }

    func testPersistFailureDoesNotLeakUncommittedMutation() async throws {
        let transport = FakeTransport()
        let (repository, _, store) = try makeRepository(transport: transport)
        store.failWrites()

        do {
            _ = try await repository.queueIssue(issue("not-persisted"))
            XCTFail("persistence failure must reject the mutation")
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .statePersistenceFailed)
        }
        let failedHistory = await repository.statusHistory()
        XCTAssertEqual(failedHistory.last?.state, .failed)
        XCTAssertEqual(failedHistory.last?.reason, .statePersistenceFailed)

        store.failWrites(false)
        _ = try await repository.save(session(), operationID: "after-failure")
        let checkpoint = await repository.checkpointSnapshot()
        XCTAssertTrue(checkpoint.envelope.issues.isEmpty)
        XCTAssertEqual(checkpoint.envelope.operations.map(\.id), ["after-failure"])
    }

    func testAccountChangeKeepsPriorAccountDataIsolatedAfterFetch() async throws {
        let transport = FakeTransport()
        let (repository, _, _) = try makeRepository(transport: transport)
        _ = try await repository.save(session())
        _ = try await repository.queueIssue(issue("prior-account"))
        try await repository.handle(.accountChanged)

        let beforeFetch = await repository.checkpointSnapshot()
        XCTAssertTrue(beforeFetch.accountIsolationRequired)
        XCTAssertEqual(beforeFetch.envelope.issues.first?.issueID, "prior-account")

        _ = try await repository.fetch()
        let afterFetch = await repository.checkpointSnapshot()
        XCTAssertTrue(afterFetch.accountIsolationRequired)
        do {
            _ = try await repository.send()
            XCTFail("account-isolated progress must never upload to the new account")
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .accountIsolationRequired)
        }
        XCTAssertEqual(transport.sendCount, 0)
        let history = await repository.statusHistory()
        XCTAssertEqual(history.last?.state, .accountIsolationRequired)
    }

    func testAccountIsolationNeverMergesFetchedRemoteProgressIntoVisibleEnvelope() async throws {
        let transport = FakeTransport()
        let (repository, _, _) = try makeRepository(transport: transport)
        _ = try await repository.save(session("local"), operationID: "local-operation")
        let local = await repository.snapshot()
        let remote = ProgressEnvelope(
            actorID: "remote-device",
            aggregate: AggregateSnapshot(sessionsTotal: 9, answered: 9, correct: 9)
        )
        transport.fetchResult = CloudProgressFetchResult(
            records: [try CloudKitMapping.snapshotRecord(remote)]
        )

        try await repository.handle(.accountChanged)
        _ = try await repository.fetch()

        let isolated = await repository.checkpointSnapshot()
        XCTAssertTrue(isolated.accountIsolationRequired)
        XCTAssertEqual(isolated.envelope.aggregate, local.aggregate)
        XCTAssertEqual(isolated.remoteRecords.map(\.recordName), [CloudKitContract.snapshotRecordName])
    }

    func testAccountChangeRecoversWhenFullSnapshotExactlyAcknowledgesRetainedOperations() async throws {
        let first = ProgressOperation(
            operationID: "acknowledged-1",
            createdAt: Date(timeIntervalSince1970: 1_001),
            status: .applied,
            session: session("acknowledged-session-1"),
            serverRevision: 1
        )
        let second = ProgressOperation(
            operationID: "acknowledged-2",
            createdAt: Date(timeIntervalSince1970: 1_002),
            status: .applied,
            session: session("acknowledged-session-2"),
            serverRevision: 2
        )
        let localEnvelope = ProgressEnvelope(
            documentRevision: 2,
            actorID: "device-a",
            operationID: second.id,
            sessionDetails: [first.session, second.session].compactMap { $0 },
            aggregate: AggregateSnapshot(sessionsTotal: 2, answered: 2, correct: 2),
            operations: [first, second]
        )
        let store = CloudProgressMemoryStore(checkpoint: CloudProgressCheckpoint(
            envelope: localEnvelope,
            sentOperationIDs: [first.id, second.id]
        ))
        let transport = FakeTransport()
        transport.fetchResult = CloudProgressFetchResult(
            records: [try CloudKitMapping.snapshotRecord(localEnvelope)]
        )
        let (repository, _, _) = try makeRepository(transport: transport, store: store)
        try await repository.handle(.accountChanged)

        _ = try await repository.fetch()

        let recovered = await repository.checkpointSnapshot()
        XCTAssertFalse(recovered.accountIsolationRequired)
        XCTAssertFalse(recovered.requiresRebase)
        XCTAssertFalse(recovered.snapshotDirty)
        let pendingRecords = try await repository.pendingRecords()
        XCTAssertEqual(pendingRecords, [])
    }

    func testAccountChangeStaysIsolatedWhenFullSnapshotOmitsRetainedOperation() async throws {
        let retained = ProgressOperation(
            operationID: "acknowledged-retained",
            createdAt: Date(timeIntervalSince1970: 1_001),
            status: .applied,
            session: session("acknowledged-retained-session"),
            serverRevision: 1
        )
        let missing = ProgressOperation(
            operationID: "acknowledged-missing",
            createdAt: Date(timeIntervalSince1970: 1_002),
            status: .applied,
            session: session("acknowledged-missing-session"),
            serverRevision: 2
        )
        let localEnvelope = ProgressEnvelope(
            documentRevision: 2,
            actorID: "device-a",
            operationID: missing.id,
            sessionDetails: [retained.session, missing.session].compactMap { $0 },
            aggregate: AggregateSnapshot(sessionsTotal: 2, answered: 2, correct: 2),
            operations: [retained, missing]
        )
        let authoritativeEnvelope = ProgressEnvelope(
            documentRevision: 1,
            actorID: "remote-device",
            operationID: retained.id,
            sessionDetails: [retained.session].compactMap { $0 },
            aggregate: AggregateSnapshot(sessionsTotal: 1, answered: 1, correct: 1),
            operations: [retained]
        )
        let store = CloudProgressMemoryStore(checkpoint: CloudProgressCheckpoint(
            envelope: localEnvelope,
            sentOperationIDs: [retained.id, missing.id]
        ))
        let transport = FakeTransport()
        transport.fetchResult = CloudProgressFetchResult(
            records: [try CloudKitMapping.snapshotRecord(authoritativeEnvelope)]
        )
        let (repository, _, _) = try makeRepository(transport: transport, store: store)
        try await repository.handle(.accountChanged)

        _ = try await repository.fetch()

        let isolated = await repository.checkpointSnapshot()
        XCTAssertTrue(isolated.accountIsolationRequired)
        XCTAssertTrue(isolated.requiresRebase)
        XCTAssertTrue(isolated.snapshotDirty)
    }

    func testAccountChangeStaysIsolatedWhenFullSnapshotChangesRetainedOperation() async throws {
        let retained = ProgressOperation(
            operationID: "acknowledged-changed",
            createdAt: Date(timeIntervalSince1970: 1_001),
            status: .applied,
            session: session("original-session"),
            serverRevision: 1
        )
        let changed = ProgressOperation(
            operationID: retained.id,
            createdAt: retained.createdAt,
            status: retained.status,
            session: session("changed-session"),
            serverRevision: retained.serverRevision
        )
        let localEnvelope = ProgressEnvelope(
            documentRevision: 1,
            actorID: "device-a",
            operationID: retained.id,
            sessionDetails: [retained.session].compactMap { $0 },
            aggregate: AggregateSnapshot(sessionsTotal: 1, answered: 1, correct: 1),
            operations: [retained]
        )
        let authoritativeEnvelope = ProgressEnvelope(
            documentRevision: 1,
            actorID: "remote-device",
            operationID: changed.id,
            sessionDetails: [changed.session].compactMap { $0 },
            aggregate: AggregateSnapshot(sessionsTotal: 1, answered: 1, correct: 1),
            operations: [changed]
        )
        let store = CloudProgressMemoryStore(checkpoint: CloudProgressCheckpoint(
            envelope: localEnvelope,
            sentOperationIDs: [retained.id]
        ))
        let transport = FakeTransport()
        transport.fetchResult = CloudProgressFetchResult(
            records: [try CloudKitMapping.snapshotRecord(authoritativeEnvelope)]
        )
        let (repository, _, _) = try makeRepository(transport: transport, store: store)
        try await repository.handle(.accountChanged)

        _ = try await repository.fetch()

        let isolated = await repository.checkpointSnapshot()
        XCTAssertTrue(isolated.accountIsolationRequired)
        XCTAssertTrue(isolated.requiresRebase)
        XCTAssertTrue(isolated.snapshotDirty)
    }

    func testAccountChangeBlankCheckpointRecoversAfterEmptyFullFetch() async throws {
        let transport = FakeTransport()
        let (repository, _, _) = try makeRepository(transport: transport)
        try await repository.handle(.accountChanged)

        let beforeFetch = await repository.checkpointSnapshot()
        XCTAssertTrue(beforeFetch.accountIsolationRequired)
        XCTAssertTrue(beforeFetch.requiresRebase)
        XCTAssertTrue(beforeFetch.snapshotDirty)

        _ = try await repository.fetch()

        let afterFetch = await repository.checkpointSnapshot()
        XCTAssertFalse(afterFetch.accountIsolationRequired)
        XCTAssertFalse(afterFetch.requiresRebase)
        XCTAssertTrue(afterFetch.snapshotDirty)

        _ = try await repository.send()
        XCTAssertEqual(transport.sendCount, 1)
        XCTAssertEqual(transport.sendRecordNames.last, [CloudKitContract.snapshotRecordName])
    }

    func testAuthorizedImportReleasesIsolationOnlyAfterEmptyFullFetch() async throws {
        let transport = FakeTransport()
        let (repository, _, _) = try makeRepository(transport: transport)
        _ = try await repository.save(session(), operationID: "legacy-session-session-1")
        try await repository.handle(.accountChanged)

        do {
            try await repository.releaseAccountIsolationForAuthorizedImport(
                expectedOperationIDs: ["legacy-session-session-1"]
            )
            XCTFail("import authorization must require a completed full fetch")
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .accountIsolationRequired)
        }

        _ = try await repository.fetch()
        let isolated = await repository.checkpointSnapshot()
        XCTAssertTrue(isolated.accountIsolationRequired)
        XCTAssertTrue(isolated.requiresRebase)

        try await repository.releaseAccountIsolationForAuthorizedImport(
            expectedOperationIDs: ["legacy-session-session-1"]
        )
        let released = await repository.checkpointSnapshot()
        XCTAssertFalse(released.accountIsolationRequired)
        XCTAssertFalse(released.requiresRebase)

        _ = try await repository.send()
        XCTAssertEqual(transport.sendCount, 1)
    }

    func testAuthorizedImportRejectsAlreadyAcknowledgedPriorAccountOperation() async throws {
        let transport = FakeTransport()
        let (repository, _, _) = try makeRepository(transport: transport)
        _ = try await repository.save(session(), operationID: "legacy-session-session-1")
        _ = try await repository.send()
        try await repository.handle(.accountChanged)
        _ = try await repository.fetch()

        do {
            try await repository.releaseAccountIsolationForAuthorizedImport(
                expectedOperationIDs: ["legacy-session-session-1"]
            )
            XCTFail("a prior-account acknowledgement must not authorize import")
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .accountIsolationRequired)
        }
    }

    func testFetchedRemoteDataInvalidatesAnEarlierEmptyImportBaseline() async throws {
        let transport = FakeTransport()
        let (repository, _, _) = try makeRepository(transport: transport)
        _ = try await repository.save(session(), operationID: "legacy-session-session-1")
        try await repository.handle(.accountChanged)
        _ = try await repository.fetch()

        let remote = ProgressEnvelope(
            actorID: "remote-device",
            aggregate: AggregateSnapshot(sessionsTotal: 1, answered: 1, correct: 1)
        )
        try await repository.handle(.fetched([try CloudKitMapping.snapshotRecord(remote)]))

        do {
            try await repository.releaseAccountIsolationForAuthorizedImport(
                expectedOperationIDs: ["legacy-session-session-1"]
            )
            XCTFail("new remote data must invalidate an earlier empty baseline")
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .accountIsolationRequired)
        }
    }

    func testAuthorizedImportRejectsNonEmptyFullRemoteBaseline() async throws {
        let transport = FakeTransport()
        transport.fetchResult = CloudProgressFetchResult(
            records: [try CloudKitMapping.snapshotRecord(
                ProgressEnvelope(
                    actorID: "remote-device",
                    aggregate: AggregateSnapshot(sessionsTotal: 1, answered: 1, correct: 1)
                )
            )]
        )
        let (repository, _, _) = try makeRepository(transport: transport)
        _ = try await repository.save(session(), operationID: "legacy-session-session-1")
        try await repository.handle(.accountChanged)

        _ = try await repository.fetch()
        do {
            try await repository.releaseAccountIsolationForAuthorizedImport(
                expectedOperationIDs: ["legacy-session-session-1"]
            )
            XCTFail("a non-empty remote baseline must remain isolated")
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .accountIsolationRequired)
        }
        XCTAssertEqual(transport.sendCount, 0)
    }

    func testFilePersistenceDistinguishesStickyCorruptionFromRetryableUnavailable() throws {
        let corruptURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("QuizzlerKit-corrupt-\(UUID().uuidString)")
        let unavailableURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("QuizzlerKit-unavailable-\(UUID().uuidString)")
        defer {
            try? FileManager.default.removeItem(at: corruptURL)
            try? FileManager.default.removeItem(at: unavailableURL)
        }

        try Data("{not-json".utf8).write(to: corruptURL)
        XCTAssertThrowsError(try CloudProgressFileStore(url: corruptURL).load()) { error in
            XCTAssertEqual(error as? CloudProgressRepositoryError, .corruptState)
        }

        try FileManager.default.createDirectory(at: unavailableURL, withIntermediateDirectories: false)
        XCTAssertThrowsError(try CloudProgressFileStore(url: unavailableURL).load()) { error in
            XCTAssertEqual(error as? CloudProgressRepositoryError, .persistenceUnavailable)
        }
    }

    func testDeliveredIssuesDoNotReturnViaFullFetch() async throws {
        let transport = FakeTransport()
        let (repositoryA, _, _) = try makeRepository(transport: transport)
        _ = try await repositoryA.queueIssue(issue("issue-delivered"))
        _ = try await repositoryA.send()

        let publishedSnapshot = try XCTUnwrap(transport.lastPublishedSnapshotRecord)
        transport.fetchResult = CloudProgressFetchResult(
            records: [publishedSnapshot],
            snapshotChangeTag: "published-tag",
            isFullSnapshot: true
        )

        _ = try await repositoryA.fetch(full: true)
        let snapshotA = await repositoryA.snapshot()
        XCTAssertTrue(snapshotA.issues.isEmpty)

        let (repositoryB, _, _) = try makeRepository(transport: transport)
        _ = try await repositoryB.fetch(full: true)
        let snapshotB = await repositoryB.snapshot()
        XCTAssertTrue(snapshotB.issues.isEmpty)
    }

    func testOverFullIssueQueueDegradesIssueSyncOnly() async throws {
        let transport = FakeTransport()
        let store = CloudProgressMemoryStore()
        let (repository, _, _) = try makeRepository(transport: transport, store: store)

        for i in 0..<CloudKitContract.maximumQueuedIssues {
            _ = try await repository.queueIssue(issue("issue-\(i)"))
        }
        _ = try await repository.save(session("session-1"))

        let remoteIssues = try (0..<CloudKitContract.maximumQueuedIssues).map {
            try issue("remote-old-issue-\($0)")
        }
        let oldStyleEnvelope = ProgressEnvelope(
            documentRevision: 1,
            actorID: "remote-device",
            issues: remoteIssues
        )
        let payload = try JSONEncoder().encode(oldStyleEnvelope)
        let remoteSnapshot = try CloudKitMappedRecord(
            kind: .snapshot,
            recordName: CloudKitContract.snapshotRecordName,
            fields: [
                "schema_version": .integer(CloudKitMapping.schemaVersion),
                "document_revision": .integer(1),
                "actor_id": .string("remote-device"),
                "compaction_watermark_revision": .integer(0),
                "payload": .data(payload)
            ]
        )

        transport.seedAuthoritativeRevision(1)
        transport.fetchResult = CloudProgressFetchResult(
            records: [remoteSnapshot],
            snapshotChangeTag: "remote-tag",
            isFullSnapshot: true
        )
        _ = try await repository.fetch(full: true)

        do {
            _ = try await repository.queueIssue(issue("overflow-129"))
            XCTFail("the 129th queueIssue must throw issueQueueFull")
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .issueQueueFull)
        }

        transport.issueSendResult = CloudProgressSendResult()
        _ = try await repository.send()

        let publishedSnapshot = try XCTUnwrap(transport.lastPublishedSnapshotRecord)
        let decodedEnvelope = try CloudKitMapping.snapshot(from: publishedSnapshot)
        XCTAssertTrue(decodedEnvelope.issues.isEmpty)
        guard case let .data(payloadData) = publishedSnapshot.fields["payload"] else {
            return XCTFail("payload must be data")
        }
        let rawEnvelope = try JSONDecoder().decode(ProgressEnvelope.self, from: payloadData)
        XCTAssertTrue(rawEnvelope.issues.isEmpty)
        XCTAssertGreaterThanOrEqual(decodedEnvelope.documentRevision, 1)
        let checkpointAfterSend = await repository.checkpointSnapshot()
        XCTAssertGreaterThan(checkpointAfterSend.envelope.documentRevision, 1)

        let issueFailures = Dictionary(uniqueKeysWithValues: (0..<CloudKitContract.maximumQueuedIssues).map {
            ("QuestionIssue/issue-\($0)", CloudProgressRecordFailure(recordName: "QuestionIssue/issue-\($0)", reason: .permissionDenied, retryable: false))
        })
        transport.issueSendResult = nil
        transport.issueRecordFailures = issueFailures

        do {
            _ = try await repository.send()
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .partialFailure)
        }

        _ = try await repository.save(session("session-2"))
        _ = try await repository.send()

        let secondPublishedSnapshot = try XCTUnwrap(transport.lastPublishedSnapshotRecord)
        let secondDecoded = try CloudKitMapping.snapshot(from: secondPublishedSnapshot)
        XCTAssertGreaterThan(secondDecoded.documentRevision, decodedEnvelope.documentRevision)

        let (reloaded, _, _) = try makeRepository(transport: transport, store: store)
        let checkpoint = await reloaded.checkpointSnapshot()
        XCTAssertEqual(checkpoint.failedIssueReasons.count, CloudKitContract.maximumQueuedIssues)
        XCTAssertEqual(checkpoint.envelope.issues.count, CloudKitContract.maximumQueuedIssues)
        XCTAssertTrue(checkpoint.envelope.operations.contains { $0.session?.sessionID == "session-2" })
    }

    func testLegacyLatchedCheckpointUnlatchesOnLoad() async throws {
        let baseCheckpoint = CloudProgressCheckpoint(
            envelope: ProgressEnvelope(actorID: "device-a")
        )
        let baseData = try JSONEncoder().encode(baseCheckpoint)
        var json = try XCTUnwrap(JSONSerialization.jsonObject(with: baseData) as? [String: Any])
        json["issueQueueConflict"] = true
        let legacyData = try JSONSerialization.data(withJSONObject: json)

        let loadedCheckpoint = try JSONDecoder().decode(CloudProgressCheckpoint.self, from: legacyData)
        let transport = FakeTransport()
        let store = CloudProgressMemoryStore(checkpoint: loadedCheckpoint)
        let (repository, _, _) = try makeRepository(transport: transport, store: store)

        _ = try await repository.save(session("session-legacy"))
        _ = try await repository.send()

        let published = try XCTUnwrap(transport.lastPublishedSnapshotRecord)
        let decoded = try CloudKitMapping.snapshot(from: published)
        XCTAssertGreaterThanOrEqual(decoded.documentRevision, 0)
        let checkpoint = await repository.checkpointSnapshot()
        XCTAssertTrue(checkpoint.envelope.operations.contains { $0.session?.sessionID == "session-legacy" })
    }

    func testReviewHistoryCombinesPaginatedFakeResponsesAndDeduplicates() async throws {
        let transport = FakeTransport()
        let first = try CloudKitMapping.reviewEventRecord(reviewEvent(operationID: "one", revision: 2))
        let second = try CloudKitMapping.reviewEventRecord(reviewEvent(operationID: "two", revision: 3))
        transport.reviewHistoryPages = [[first], [first, second]]
        let store = CloudProgressMemoryStore(checkpoint: CloudProgressCheckpoint(
            envelope: ProgressEnvelope(schemaVersion: 2, actorID: "device-a")
        ))
        let (repository, _, _) = try makeRepository(transport: transport, store: store)

        let history = try await repository.reviewHistory(for: QuestionIdentity(
            courseID: "course", packID: "pack", questionID: "q-1"
        ))

        XCTAssertFalse(history.isComplete)
        XCTAssertEqual(history.events.map(\.id), ["one:0", "two:0"])
        XCTAssertEqual(transport.reviewHistoryQueryCount, 1)
    }

    func testReviewHistoryRejectsMalformedPerRecordResponse() async throws {
        let transport = FakeTransport()
        let mapped = try CloudKitMapping.reviewEventRecord(reviewEvent())
        var fields = mapped.fields
        fields["question_key"] = .string("wrong")
        transport.reviewHistoryPages = [[try CloudKitMappedRecord(
            kind: .reviewEvent, recordName: mapped.recordName, fields: fields
        )]]
        let (repository, _, _) = try makeRepository(transport: transport)

        do {
            _ = try await repository.reviewHistory(for: QuestionIdentity(
                courseID: "course", packID: "pack", questionID: "q-1"
            ))
            XCTFail("invalid queried event must fail closed")
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .malformedRecord)
        }
    }

    func testReviewHistoryIsIncompleteForV1BaselineAndEventsDoNotEnterCheckpoint() async throws {
        let transport = FakeTransport()
        let event = try CloudKitMapping.reviewEventRecord(reviewEvent())
        transport.reviewHistoryPages = [[event]]
        let (repository, _, store) = try makeRepository(transport: transport)

        let history = try await repository.reviewHistory(for: QuestionIdentity(
            courseID: "course", packID: "pack", questionID: "q-1"
        ))
        XCTAssertFalse(history.isComplete)
        XCTAssertEqual(history.events, [reviewEvent()])

        try await repository.handle(.fetched([event]))
        let checkpoint = try XCTUnwrap(try store.load())
        XCTAssertFalse(checkpoint.remoteRecords.contains { $0.kind == .reviewEvent })
        XCTAssertTrue(checkpoint.remoteRecords.isEmpty)
    }

    func testMigratedLegacyQuestionStaysIncompleteAfterNewEvent() async throws {
        let identity = QuestionIdentity(courseID: "course", packID: "pack", questionID: "q-1")
        let legacyState = try SRSState(
            tier: 3, nextDueAt: Date(timeIntervalSince1970: 1_000),
            lastReviewedAt: Date(timeIntervalSince1970: 500), intervalDays: 7, reviewCount: 2
        )
        let legacy = ProgressEnvelope(
            actorID: "device-a", srs: [SRSSnapshot(identity: identity, state: legacyState)]
        )
        let transport = FakeTransport()
        try transport.seedAuthoritativeSnapshot(legacy)
        let store = CloudProgressMemoryStore(checkpoint: CloudProgressCheckpoint(envelope: legacy))
        let (repository, _, _) = try makeRepository(transport: transport, store: store)
        _ = try await repository.save(timedSession("after-legacy"), operationID: "new-after-legacy")
        _ = try await repository.send()

        let history = try await repository.reviewHistory(for: identity)
        let snapshot = await repository.snapshot()
        XCTAssertFalse(history.isComplete)
        XCTAssertEqual(history.events.map(\.id), ["new-after-legacy:0"])
        XCTAssertEqual(snapshot.srs.first?.state.reviewCount, 3)
    }

    func testNewQuestionHasCompleteCrossDeviceHistoryAfterOperationCompaction() async throws {
        let transport = FakeTransport()
        let (source, _, sourceStore) = try makeRepository(transport: transport)
        _ = try await source.save(timedSession("new-question"), operationID: "new-question-review")
        _ = try await source.send()
        let event = try XCTUnwrap(transport.durableEventRecords.first)
        XCTAssertTrue(try XCTUnwrap(sourceStore.load()).remoteRecords.allSatisfy { $0.kind != .reviewEvent })

        _ = try await transport.deleteChanges(["ProgressOperation/new-question-review"])
        XCTAssertEqual(transport.durableEventRecords, [event])
        var compacted = try CloudKitMapping.snapshot(from: XCTUnwrap(transport.lastPublishedSnapshotRecord))
        compacted.operations = []
        compacted.compaction.watermarkRevision = compacted.documentRevision
        try transport.seedAuthoritativeSnapshot(compacted)
        transport.fetchResult = CloudProgressFetchResult(
            records: [try XCTUnwrap(transport.lastPublishedSnapshotRecord)],
            isFullSnapshot: true
        )
        let (otherDevice, _, _) = try makeRepository(transport: transport)
        _ = try await otherDevice.fetch(full: true)
        let remoteCheckpoint = await otherDevice.checkpointSnapshot()
        XCTAssertTrue(remoteCheckpoint.envelope.operations.isEmpty)
        XCTAssertTrue(remoteCheckpoint.remoteRecords.allSatisfy { $0.kind != .reviewEvent })
        XCTAssertLessThan(try JSONEncoder().encode(remoteCheckpoint).count, 50_000)
        let identity = QuestionIdentity(courseID: "course", packID: "pack", questionID: "q-1")
        let history = try await otherDevice.reviewHistory(for: identity)
        XCTAssertTrue(history.isComplete)
        XCTAssertEqual(history.events.map(\.id), ["new-question-review:0"])
    }

    func testEventGapAfterFailedSyncRemainsIncomplete() async throws {
        let transport = FakeTransport()
        transport.failAtomicBeforeCommit = true
        let (repository, _, _) = try makeRepository(transport: transport)
        _ = try await repository.save(timedSession("failed"), operationID: "failed-review")
        do { _ = try await repository.send() } catch { }

        let history = try await repository.reviewHistory(for: QuestionIdentity(
            courseID: "course", packID: "pack", questionID: "q-1"
        ))
        XCTAssertFalse(history.isComplete)
        XCTAssertTrue(history.events.isEmpty)
    }

    func testReviewHistoryDiscardsResultWhenAccountChangesDuringQuery() async throws {
        let transport = DelayedHistoryTransport()
        let repository = try CloudProgressRepository(
            actorID: "device-a",
            persistence: CloudProgressMemoryStore(checkpoint: CloudProgressCheckpoint(
                envelope: ProgressEnvelope(schemaVersion: 2, actorID: "device-a")
            )),
            transport: transport
        )
        let query = Task {
            try await repository.reviewHistory(for: QuestionIdentity(
                courseID: "course", packID: "pack", questionID: "q-1"
            ))
        }
        await transport.waitUntilStarted()
        try await repository.handle(.accountChanged)
        await transport.resume(with: [try CloudKitMapping.reviewEventRecord(reviewEvent())])

        do {
            _ = try await query.value
            XCTFail("account change must discard an in-flight query")
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .accountIsolationRequired)
        }
    }

    func testEventOnlyZoneIsNotAnEmptyAccountRecoveryBaseline() async throws {
        let transport = FakeTransport()
        transport.fetchResult = CloudProgressFetchResult(
            isFullSnapshot: true,
            containsReviewEvents: true
        )
        let (repository, _, _) = try makeRepository(transport: transport)
        try await repository.handle(.accountChanged)

        _ = try await repository.fetch(full: true)

        let checkpoint = await repository.checkpointSnapshot()
        XCTAssertTrue(checkpoint.accountIsolationRequired)
        XCTAssertTrue(checkpoint.requiresRebase)
    }
}

private final class FakeTransport: @unchecked Sendable, CloudProgressTransport {
    private let lock = NSLock()
    private var fetchCountStorage = 0
    private var sendCountStorage = 0
    private var atomicSendCountStorage = 0
    private var resetCountStorage = 0
    private var authoritativeRevisionStorage = 0
    private var authoritativeChangeTagStorage: String?
    private var zoneExistsStorage = false
    private var lastPublishedSnapshotRecordStorage: CloudKitMappedRecord?
    private var issueRecordsStorage: [String: CloudKitMappedRecord] = [:]
    private var authoritativeOperationRecordsStorage: [String: CloudKitMappedRecord] = [:]
    private var reviewEventRecordsStorage: [String: CloudKitMappedRecord] = [:]
    private var sendRecordNamesStorage: [[String]] = []
    private var deleteRecordNamesStorage: [[String]] = []
    private var reviewHistoryQueryCountStorage = 0
    var fetchResult = CloudProgressFetchResult()
    var sendResult = CloudProgressSendResult()
    var issueSendResult: CloudProgressSendResult?
    var issueRecordFailures: [String: CloudProgressRecordFailure] = [:]
    var fetchError: Error?
    var sendError: Error?
    var reviewHistoryPages: [[CloudKitMappedRecord]] = []
    var failAtomicBeforeCommit = false
    var failAtomicOnCall: Int?
    var loseNextAtomicAcknowledgement = false

    var fetchCount: Int { lock.withLock { fetchCountStorage } }
    var sendCount: Int { lock.withLock { sendCountStorage } }
    var resetCount: Int { lock.withLock { resetCountStorage } }
    var sendRecordNames: [[String]] { lock.withLock { sendRecordNamesStorage } }
    var atomicSendCount: Int { lock.withLock { atomicSendCountStorage } }
    var zoneExists: Bool { lock.withLock { zoneExistsStorage } }
    var lastPublishedSnapshotRecord: CloudKitMappedRecord? { lock.withLock { lastPublishedSnapshotRecordStorage } }
    var authoritativeChangeTag: String? { lock.withLock { authoritativeChangeTagStorage } }
    var reviewHistoryQueryCount: Int { lock.withLock { reviewHistoryQueryCountStorage } }
    var durableEventRecords: [CloudKitMappedRecord] {
        lock.withLock { reviewEventRecordsStorage.values.sorted { $0.recordName < $1.recordName } }
    }

    func removeEvent(_ name: String) {
        lock.withLock { _ = reviewEventRecordsStorage.removeValue(forKey: name) }
    }

    func replaceEvent(_ record: CloudKitMappedRecord) {
        lock.withLock { reviewEventRecordsStorage[record.recordName] = record }
    }

    func deleteZone() {
        lock.withLock {
            zoneExistsStorage = false
            authoritativeRevisionStorage = 0
            authoritativeChangeTagStorage = nil
            lastPublishedSnapshotRecordStorage = nil
            authoritativeOperationRecordsStorage.removeAll()
            reviewEventRecordsStorage.removeAll()
        }
    }

    func seedExistingOperation(_ record: CloudKitMappedRecord) {
        lock.withLock {
            authoritativeOperationRecordsStorage[record.recordName] = record
            if case let .integer(revision) = record.fields["server_revision"] {
                zoneExistsStorage = true
                authoritativeRevisionStorage = max(authoritativeRevisionStorage, Int(revision))
            }
        }
    }

    func seedAuthoritativeRevision(_ revision: Int) {
        lock.withLock {
            zoneExistsStorage = revision > 0
            authoritativeRevisionStorage = revision
            authoritativeChangeTagStorage = nil
        }
    }
    func seedAuthoritativeSnapshot(_ envelope: ProgressEnvelope) throws {
        try lock.withLock {
            zoneExistsStorage = true
            authoritativeRevisionStorage = envelope.documentRevision
            lastPublishedSnapshotRecordStorage = try CloudKitMapping.snapshotRecord(envelope)
        }
    }
    var deleteRecordNames: [[String]] { lock.withLock { deleteRecordNamesStorage } }

    func fetchChanges() async throws -> CloudProgressFetchResult {
        try lock.withLock {
            fetchCountStorage += 1
            if let fetchError { throw fetchError }
            return fetchResult
        }
    }

    func fetchChanges(full: Bool) async throws -> CloudProgressFetchResult {
        let result = try await fetchChanges()
        guard full else { return result }
        return CloudProgressFetchResult(
            records: result.records,
            tokenExpired: result.tokenExpired,
            snapshotChangeTag: result.snapshotChangeTag,
            isFullSnapshot: true,
            containsReviewEvents: result.containsReviewEvents
        )
    }

    func fetchReviewHistory(for identity: QuestionIdentity) async throws -> [CloudKitMappedRecord] {
        try lock.withLock {
            reviewHistoryQueryCountStorage += 1
            if let fetchError { throw fetchError }
            return reviewHistoryPages.flatMap { $0 } + reviewEventRecordsStorage.values.filter { record in
                (try? CloudKitMapping.reviewEvent(from: record).identity) == identity
            }
        }
    }

    func sendChanges(_ records: [CloudKitMappedRecord]) async throws -> CloudProgressSendResult {
        try lock.withLock {
            sendCountStorage += 1
            sendRecordNamesStorage.append(records.map(\.recordName))
            if let sendError { throw sendError }
            return sendResult.savedRecordNames.isEmpty && sendResult.deletedRecordNames.isEmpty
                && sendResult.failedRecords.isEmpty && sendResult.serverRecords.isEmpty
                ? CloudProgressSendResult(savedRecordNames: records.map(\.recordName))
                : sendResult
        }
    }

    func sendIssuesAtomically(_ records: [CloudKitMappedRecord]) async throws -> CloudProgressSendResult {
        try lock.withLock {
            guard records.allSatisfy({ $0.kind == .issue }) else {
                throw CloudProgressTransportError.unavailable
            }
            sendCountStorage += 1
            sendRecordNamesStorage.append(records.map(\.recordName))
            if let sendError { throw sendError }
            if let issueSendResult { return issueSendResult }
            if !issueRecordFailures.isEmpty {
                var saved: [String] = []
                var failed: [CloudProgressRecordFailure] = []
                for record in records {
                    if let failure = issueRecordFailures[record.recordName] {
                        failed.append(failure)
                    } else {
                        saved.append(record.recordName)
                        issueRecordsStorage[record.recordName] = record
                    }
                }
                return CloudProgressSendResult(savedRecordNames: saved, failedRecords: failed)
            }
            if !sendResult.failedRecords.isEmpty { return sendResult }
            for record in records {
                if let existing = issueRecordsStorage[record.recordName], existing != record {
                    throw CloudProgressTransportError.serverRecordChanged
                }
            }
            records.forEach { issueRecordsStorage[$0.recordName] = $0 }
            return sendResult.savedRecordNames.isEmpty
                ? CloudProgressSendResult(savedRecordNames: records.map(\.recordName))
                : sendResult
        }
    }

    func sendProgressAtomically(
        _ records: [CloudKitMappedRecord],
        expectedRevision: Int,
        snapshotChangeTag: String?
    ) async throws -> CloudProgressSendResult {
        try lock.withLock {
            atomicSendCountStorage += 1
            sendCountStorage += 1
            sendRecordNamesStorage.append(records.map(\.recordName))
            if let sendError { throw sendError }
            if atomicSendCountStorage == failAtomicOnCall { throw CloudProgressTransportError.network }
            if failAtomicBeforeCommit { throw CloudProgressTransportError.network }
            guard let snapshot = records.first(where: { $0.kind == .snapshot }) else {
                throw CloudProgressTransportError.unavailable
            }
            let envelope = try CloudKitMapping.snapshot(from: snapshot)
            guard envelope.documentRevision == expectedRevision else {
                throw CloudProgressTransportError.serverRecordChanged
            }
            let incomingOperationRecords = records.filter { $0.kind == .operation }
            var assigned: [String: Int] = [:]
            var newOperationIDs: [String] = []
            for record in incomingOperationRecords {
                if let existing = authoritativeOperationRecordsStorage[record.recordName] {
                    var incomingOperation = try CloudKitMapping.operation(from: record)
                    var existingOperation = try CloudKitMapping.operation(from: existing)
                    incomingOperation.serverRevision = nil
                    existingOperation.serverRevision = nil
                    guard incomingOperation == existingOperation else {
                        throw CloudProgressTransportError.serverRecordChanged
                    }
                    guard case let .integer(revision) = existing.fields["server_revision"] else {
                        throw CloudProgressTransportError.serverRecordChanged
                    }
                    assigned[record.recordName] = Int(revision)
                } else {
                    guard record.fields["server_revision"] == nil else {
                        throw CloudProgressTransportError.serverRecordChanged
                    }
                    newOperationIDs.append(record.recordName)
                }
            }
            let snapshotOperationIDs = envelope.operations.compactMap { operation in
                operation.serverRevision == nil ? operation.id : nil
            }
            guard Set(snapshotOperationIDs).isSuperset(of: Set(newOperationIDs.compactMap { name in
                name.split(separator: "/", maxSplits: 1).last.map(String.init)
            })), snapshotOperationIDs.allSatisfy({ id in
                newOperationIDs.contains("ProgressOperation/\(id)")
                    || assigned["ProgressOperation/\(id)"] != nil
            }) else {
                throw CloudProgressTransportError.serverRecordChanged
            }
            if expectedRevision != authoritativeRevisionStorage {
                guard newOperationIDs.isEmpty else { throw CloudProgressTransportError.serverRecordChanged }
            } else if authoritativeChangeTagStorage != nil,
                      snapshotChangeTag != authoritativeChangeTagStorage {
                throw CloudProgressTransportError.serverRecordChanged
            }
            let newAssignments = Dictionary(uniqueKeysWithValues: newOperationIDs.sorted().enumerated().map {
                ($0.element, expectedRevision + $0.offset + 1)
            })
            assigned.merge(newAssignments, uniquingKeysWith: { _, right in right })
            var replay = try lastPublishedSnapshotRecordStorage.map { try CloudKitMapping.snapshot(from: $0) }
                ?? ProgressEnvelope(actorID: envelope.actorID, createdAt: envelope.createdAt)
            var newEventRecords: [CloudKitMappedRecord] = []
            var newEventCounts: [String: Int] = [:]
            for name in newOperationIDs.sorted() {
                guard let record = incomingOperationRecords.first(where: { $0.recordName == name }),
                      let revision = assigned[name] else { throw CloudProgressTransportError.unavailable }
                var operation = try CloudKitMapping.operation(from: record)
                operation.serverRevision = revision
                let derived = replay.applying(operation)
                let events = operation.kind == .review
                    ? derived.filter { operation.session?.answers[$0.ordinal].answeredAt != nil }
                    : derived
                newEventCounts[name] = events.count
                newEventRecords += try events.map(CloudKitMapping.reviewEventRecord)
            }
            guard 1 + newOperationIDs.count + newEventRecords.count <= CloudKitContract.maximumRecordsPerBatch else {
                throw CloudProgressTransportError.unavailable
            }
            sendRecordNamesStorage[sendRecordNamesStorage.count - 1] += newEventRecords.map(\.recordName)
            var verifiedEventNames: [String] = []
            for record in incomingOperationRecords where !newOperationIDs.contains(record.recordName) {
                let operation = try CloudKitMapping.operation(from: record)
                let expectedOrdinals: [Int]
                if operation.kind == .setMaximumLeitnerLevel {
                    guard let published = authoritativeOperationRecordsStorage[record.recordName],
                          case let .integer(count)? = published.fields["event_count"],
                          case let .string(digest)? = published.fields["event_digest"],
                          count >= 0,
                          count <= CloudKitContract.maximumRecordsPerBatch - 2 else {
                        throw CloudProgressTransportError.serverRecordChanged
                    }
                    expectedOrdinals = Array(0..<Int(count))
                    _ = digest
                } else {
                    expectedOrdinals = operation.session?.answers.indices.filter {
                        operation.session?.answers[$0].answeredAt != nil
                    } ?? []
                }
                var seenIdentities: Set<QuestionIdentity> = []
                var verifiedRecords: [CloudKitMappedRecord] = []
                for ordinal in expectedOrdinals {
                    let name = "QuestionReviewEvent/\(operation.id):\(ordinal)"
                    guard let saved = reviewEventRecordsStorage[name],
                          let event = try? CloudKitMapping.reviewEvent(from: saved),
                          event.operationID == operation.id,
                          event.serverRevision == assigned[record.recordName] else {
                        throw CloudProgressTransportError.serverRecordChanged
                    }
                    if operation.kind == .review {
                        guard let answer = operation.session?.answers[ordinal],
                              event.identity == answer.identity,
                              event.eventTime == answer.answeredAt,
                              event.outcome == (answer.correct ? .correct : .missed) else {
                            throw CloudProgressTransportError.serverRecordChanged
                        }
                    } else {
                        guard let maximum = operation.maximumLeitnerLevel,
                              event.ordinal == ordinal,
                              event.eventTime == operation.updatedAt,
                              event.outcome == .maximumLevelChanged,
                              event.priorLevel > maximum,
                              event.resultingLevel == maximum,
                              seenIdentities.insert(event.identity).inserted else {
                            throw CloudProgressTransportError.serverRecordChanged
                        }
                    }
                    verifiedRecords.append(saved)
                    verifiedEventNames.append(name)
                }
                if operation.kind == .setMaximumLeitnerLevel {
                    guard let published = authoritativeOperationRecordsStorage[record.recordName],
                          case let .string(digest)? = published.fields["event_digest"],
                          try CloudKitMapping.reviewEventDigest(verifiedRecords) == digest else {
                        throw CloudProgressTransportError.serverRecordChanged
                    }
                }
            }
            let usesDefaultResult = sendResult.savedRecordNames.isEmpty && sendResult.deletedRecordNames.isEmpty
                && sendResult.failedRecords.isEmpty && sendResult.serverRecords.isEmpty
            let successfulCustomResult = !sendResult.savedRecordNames.isEmpty
                && sendResult.failedRecords.isEmpty
                && sendResult.serverRecords.isEmpty
            guard usesDefaultResult || successfulCustomResult else { return sendResult }
            if newOperationIDs.isEmpty && !incomingOperationRecords.isEmpty {
                guard authoritativeRevisionStorage == assigned.values.max() else {
                    throw CloudProgressTransportError.serverRecordChanged
                }
                return CloudProgressSendResult(
                    savedRecordNames: records.map(\.recordName) + verifiedEventNames,
                    snapshotChangeTag: authoritativeChangeTagStorage,
                    assignedRevisions: Dictionary(uniqueKeysWithValues: incomingOperationRecords.compactMap { record -> (String, Int)? in
                        guard case let .string(id) = record.fields["operation_id"],
                              let revision = assigned[record.recordName] else { return nil }
                        return (id, revision)
                    })
                )
            }
            if newOperationIDs.isEmpty, expectedRevision != authoritativeRevisionStorage {
                return CloudProgressSendResult(
                    savedRecordNames: records.map(\.recordName) + verifiedEventNames,
                    snapshotChangeTag: authoritativeChangeTagStorage,
                    assignedRevisions: Dictionary(uniqueKeysWithValues: incomingOperationRecords.compactMap { record -> (String, Int)? in
                        guard case let .string(id) = record.fields["operation_id"],
                              let revision = assigned[record.recordName] else { return nil }
                        return (id, revision)
                    })
                )
            }
            authoritativeRevisionStorage = max(expectedRevision, assigned.values.max() ?? expectedRevision)
            authoritativeChangeTagStorage = "atomic-\(atomicSendCountStorage)"
            zoneExistsStorage = true
            var published = envelope
            if !newOperationIDs.isEmpty {
                published.sessionDetails = replay.sessionDetails
                published.aggregate = replay.aggregate
                published.mastery = replay.mastery
                published.srs = replay.srs
                published.maximumLeitnerLevel = replay.maximumLeitnerLevel
                published.operations = replay.operations
                published.schemaVersion = max(published.schemaVersion, replay.schemaVersion)
            }
            for index in published.operations.indices {
                let name = "ProgressOperation/\(published.operations[index].id)"
                if let revision = assigned[name] {
                    published.operations[index].serverRevision = revision
                }
            }
            published.documentRevision = authoritativeRevisionStorage
            lastPublishedSnapshotRecordStorage = try CloudKitMapping.snapshotRecord(published)
            for event in newEventRecords { reviewEventRecordsStorage[event.recordName] = event }
            for record in incomingOperationRecords {
                var fields = record.fields
                if let revision = assigned[record.recordName] {
                    fields["server_revision"] = .integer(Int64(revision))
                    if let count = newEventCounts[record.recordName],
                       (try CloudKitMapping.operation(from: record)).kind == .setMaximumLeitnerLevel {
                        fields["event_count"] = .integer(Int64(count))
                        fields["event_digest"] = .string(try CloudKitMapping.reviewEventDigest(
                            newEventRecords.filter { $0.fields["operation_id"] == record.fields["operation_id"] }
                        ))
                    }
                    authoritativeOperationRecordsStorage[record.recordName] = try CloudKitMappedRecord(
                        kind: record.kind,
                        recordName: record.recordName,
                        fields: fields
                    )
                }
            }
            let assignedOperationIDs = Dictionary(uniqueKeysWithValues: incomingOperationRecords.compactMap { record -> (String, Int)? in
                guard case let .string(operationID) = record.fields["operation_id"],
                      let revision = assigned[record.recordName] else { return nil }
                return (operationID, revision)
            })
            if loseNextAtomicAcknowledgement {
                loseNextAtomicAcknowledgement = false
                throw CloudProgressTransportError.network
            }
            return CloudProgressSendResult(
                savedRecordNames: usesDefaultResult ? records.map(\.recordName) + newEventRecords.map(\.recordName) + verifiedEventNames : sendResult.savedRecordNames,
                snapshotChangeTag: "atomic-\(atomicSendCountStorage)",
                assignedRevisions: assignedOperationIDs
            )
        }
    }

    func deleteChanges(_ recordNames: [String]) async throws -> CloudProgressSendResult {
        lock.withLock {
            deleteRecordNamesStorage.append(recordNames)
            for name in recordNames { authoritativeOperationRecordsStorage.removeValue(forKey: name) }
            return CloudProgressSendResult(deletedRecordNames: recordNames)
        }
    }

    func resetPendingChanges() async { lock.withLock { resetCountStorage += 1 } }
}

private actor DelayedHistoryTransport: CloudProgressTransport {
    private var continuation: CheckedContinuation<[CloudKitMappedRecord], Error>?
    private var started = false
    private var startWaiters: [CheckedContinuation<Void, Never>] = []

    func fetchChanges() async throws -> CloudProgressFetchResult { CloudProgressFetchResult() }
    func sendChanges(_ records: [CloudKitMappedRecord]) async throws -> CloudProgressSendResult {
        CloudProgressSendResult(savedRecordNames: records.map(\.recordName))
    }
    func resetPendingChanges() async {}

    func fetchReviewHistory(for identity: QuestionIdentity) async throws -> [CloudKitMappedRecord] {
        started = true
        let waiters = startWaiters
        startWaiters.removeAll()
        waiters.forEach { $0.resume() }
        return try await withCheckedThrowingContinuation { continuation in
            self.continuation = continuation
        }
    }

    func waitUntilStarted() async {
        guard !started else { return }
        await withCheckedContinuation { continuation in
            startWaiters.append(continuation)
        }
    }

    func resume(with records: [CloudKitMappedRecord]) {
        continuation?.resume(returning: records)
        continuation = nil
    }
}

private final class SerializedRevisionTransport: @unchecked Sendable, CloudProgressTransport {
    private let lock = NSLock()
    private var revision = 0
    private var changeTag: String?

    func fetchChanges() async throws -> CloudProgressFetchResult { CloudProgressFetchResult() }

    func sendChanges(_ records: [CloudKitMappedRecord]) async throws -> CloudProgressSendResult {
        CloudProgressSendResult(savedRecordNames: records.map(\.recordName))
    }

    func sendProgressAtomically(
        _ records: [CloudKitMappedRecord],
        expectedRevision: Int,
        snapshotChangeTag: String?
    ) async throws -> CloudProgressSendResult {
        try lock.withLock {
            guard expectedRevision == revision else {
                throw CloudProgressTransportError.serverRecordChanged
            }
            guard let snapshot = records.first(where: { $0.kind == .snapshot }),
                  try CloudKitMapping.snapshot(from: snapshot).documentRevision == expectedRevision,
                  snapshotChangeTag == changeTag else {
                throw CloudProgressTransportError.serverRecordChanged
            }
            let operationIDs = records.compactMap { record -> String? in
                guard record.kind == .operation,
                      case let .string(operationID) = record.fields["operation_id"] else { return nil }
                return operationID
            }.sorted()
            let assigned = Dictionary(uniqueKeysWithValues: operationIDs.enumerated().map {
                ($0.element, revision + $0.offset + 1)
            })
            let snapshotIDs = try CloudKitMapping.snapshot(from: records[0]).operations.compactMap {
                $0.serverRevision == nil ? $0.id : nil
            }
            guard Set(snapshotIDs) == Set(operationIDs) else {
                throw CloudProgressTransportError.serverRecordChanged
            }
            revision += assigned.count
            changeTag = "revision-\(revision)"
            return CloudProgressSendResult(
                savedRecordNames: records.map(\.recordName),
                snapshotChangeTag: changeTag,
                assignedRevisions: assigned
            )
        }
    }

    func resetPendingChanges() async {}
}

private final class FailingCheckpointStore: @unchecked Sendable, CloudProgressPersistence {
    private let lock = NSLock()
    private var checkpoint: CloudProgressCheckpoint
    private var saveCount = 0
    private let failOnSave: Int

    init(initial: CloudProgressCheckpoint, failOnSave: Int) {
        self.checkpoint = initial
        self.failOnSave = failOnSave
    }

    func load() throws -> CloudProgressCheckpoint? { lock.withLock { checkpoint } }

    func save(_ checkpoint: CloudProgressCheckpoint) throws {
        try lock.withLock {
            saveCount += 1
            if saveCount == failOnSave {
                throw CloudProgressRepositoryError.statePersistenceFailed
            }
            self.checkpoint = checkpoint
        }
    }
}
