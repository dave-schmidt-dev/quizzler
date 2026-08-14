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
        XCTAssertTrue(transport.sendRecordNames.allSatisfy { $0.count < 250 })
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

        do {
            try await repository.handle(.fetched([remoteSnapshot]))
            XCTFail("the local issue must be visibly deferred when the remote queue is full")
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .issueQueueFull)
        }
        let checkpoint = try XCTUnwrap(try store.load())
        XCTAssertFalse(checkpoint.requiresRebase)
        XCTAssertTrue(checkpoint.issueQueueConflict)
        XCTAssertTrue(checkpoint.envelope.issues.contains { $0.issueID == "local-overflow" })
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
        XCTAssertTrue(afterIssueAck.snapshotDirty)
        let pendingAfterIssueAck = try await repository.pendingRecords()
        XCTAssertEqual(pendingAfterIssueAck.map(\.recordName), [CloudKitContract.snapshotRecordName])
        let history = await repository.statusHistory()
        XCTAssertFalse(history.contains { $0.state == .synced && $0.reason == .completed })

        transport.sendResult = CloudProgressSendResult(savedRecordNames: [CloudKitContract.snapshotRecordName])
        _ = try await repository.send()
        let finalCheckpoint = await repository.checkpointSnapshot()
        XCTAssertFalse(finalCheckpoint.snapshotDirty)
        let reloaded = try CloudProgressRepository(
            actorID: "device-a",
            persistence: store,
            transport: transport
        )
        let reloadedCheckpoint = await reloaded.checkpointSnapshot()
        XCTAssertEqual(reloadedCheckpoint.envelope, finalCheckpoint.envelope)
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
        do {
            _ = try await repository.send()
            XCTFail("terminal issue failure requires manual resolution")
        } catch let error as CloudProgressRepositoryError {
            XCTAssertEqual(error, .partialFailure)
        }
        XCTAssertEqual(transport.sendCount, sendCountAfterFailure)
        let afterRetryHistory = await repository.statusHistory()
        XCTAssertFalse(afterRetryHistory.contains { $0.state == .synced && $0.reason == .completed })
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
        XCTAssertEqual(Set(checkpoint.envelope.issues.map(\.issueID)), Set(["local-issue", "remote-issue"]))
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
    private var issueRecordsStorage: [String: CloudKitMappedRecord] = [:]
    private var authoritativeOperationRecordsStorage: [String: CloudKitMappedRecord] = [:]
    private var sendRecordNamesStorage: [[String]] = []
    private var deleteRecordNamesStorage: [[String]] = []
    var fetchResult = CloudProgressFetchResult()
    var sendResult = CloudProgressSendResult()
    var fetchError: Error?
    var sendError: Error?

    var fetchCount: Int { lock.withLock { fetchCountStorage } }
    var sendCount: Int { lock.withLock { sendCountStorage } }
    var resetCount: Int { lock.withLock { resetCountStorage } }
    var sendRecordNames: [[String]] { lock.withLock { sendRecordNamesStorage } }
    var atomicSendCount: Int { lock.withLock { atomicSendCountStorage } }
    var zoneExists: Bool { lock.withLock { zoneExistsStorage } }

    func deleteZone() {
        lock.withLock {
            zoneExistsStorage = false
            authoritativeRevisionStorage = 0
            authoritativeChangeTagStorage = nil
            authoritativeOperationRecordsStorage.removeAll()
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
            isFullSnapshot: true
        )
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
            guard let snapshot = records.first(where: { $0.kind == .snapshot }) else {
                throw CloudProgressTransportError.unavailable
            }
            let envelope = try CloudKitMapping.snapshot(from: snapshot)
            guard envelope.documentRevision == expectedRevision,
                  authoritativeChangeTagStorage == nil || snapshotChangeTag == authoritativeChangeTagStorage else {
                throw CloudProgressTransportError.serverRecordChanged
            }
            let incomingOperationRecords = records.filter { $0.kind == .operation }
            var assigned: [String: Int] = [:]
            var newOperationIDs: [String] = []
            for record in incomingOperationRecords {
                if let existing = authoritativeOperationRecordsStorage[record.recordName] {
                    guard existing == record || record.fields["server_revision"] == existing.fields["server_revision"] else {
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
            guard Set(snapshotOperationIDs) == Set(newOperationIDs.compactMap { name in
                name.split(separator: "/", maxSplits: 1).last.map(String.init)
            }),
                  expectedRevision == authoritativeRevisionStorage else {
                throw CloudProgressTransportError.serverRecordChanged
            }
            let newAssignments = Dictionary(uniqueKeysWithValues: newOperationIDs.sorted().enumerated().map {
                ($0.element, expectedRevision + $0.offset + 1)
            })
            assigned.merge(newAssignments, uniquingKeysWith: { _, right in right })
            let usesDefaultResult = sendResult.savedRecordNames.isEmpty && sendResult.deletedRecordNames.isEmpty
                && sendResult.failedRecords.isEmpty && sendResult.serverRecords.isEmpty
            let successfulCustomResult = !sendResult.savedRecordNames.isEmpty
                && sendResult.failedRecords.isEmpty
                && sendResult.serverRecords.isEmpty
            guard usesDefaultResult || successfulCustomResult else { return sendResult }
            authoritativeRevisionStorage = max(expectedRevision, assigned.values.max() ?? expectedRevision)
            authoritativeChangeTagStorage = "atomic-\(atomicSendCountStorage)"
            zoneExistsStorage = true
            for record in incomingOperationRecords {
                var fields = record.fields
                if let revision = assigned[record.recordName] {
                    fields["server_revision"] = .integer(Int64(revision))
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
            return CloudProgressSendResult(
                savedRecordNames: usesDefaultResult ? records.map(\.recordName) : sendResult.savedRecordNames,
                snapshotChangeTag: "atomic-\(atomicSendCountStorage)",
                assignedRevisions: assignedOperationIDs
            )
        }
    }

    func deleteChanges(_ recordNames: [String]) async throws -> CloudProgressSendResult {
        lock.withLock {
            deleteRecordNamesStorage.append(recordNames)
            return CloudProgressSendResult(deletedRecordNames: recordNames)
        }
    }

    func resetPendingChanges() async { lock.withLock { resetCountStorage += 1 } }
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
