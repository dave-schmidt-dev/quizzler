import CryptoKit
import Foundation
import XCTest
@testable import QuizzlerKit

private struct FixtureCASConflict: Error {
    let revision: Int
    let changeTag: String?
}

private struct FixtureCASResult {
    let revision: Int
    let changeTag: String
    let assignedRevisions: [String: Int]
}

/// A deliberately small fake of the only CloudKit property the corpus needs:
/// one snapshot CAS serializes revisions and returns the next change tag.
private final class FixtureCASCloud {
    private var revision = 0
    private var changeTag: String?
    private var zoneExists = false
    private var issueIDs: Set<String> = []

    func fetch() -> (revision: Int, changeTag: String?) { (revision, changeTag) }

    func commit(
        operationIDs: [String],
        expectedRevision: Int,
        expectedChangeTag: String?
    ) throws -> FixtureCASResult {
        guard expectedRevision == revision, expectedChangeTag == changeTag else {
            throw FixtureCASConflict(revision: revision, changeTag: changeTag)
        }
        let assigned = Dictionary(uniqueKeysWithValues: operationIDs
            .sorted { Array($0.utf8).lexicographicallyPrecedes(Array($1.utf8)) }
            .enumerated()
            .map { ($0.element, revision + $0.offset + 1) })
        revision += operationIDs.count
        let nextTag = "change-tag-\(revision)"
        changeTag = nextTag
        zoneExists = true
        return FixtureCASResult(revision: revision, changeTag: nextTag, assignedRevisions: assigned)
    }

    func deleteZone() {
        revision = 0
        changeTag = nil
        zoneExists = false
        issueIDs = []
    }

    func fullFetch() -> (isFullSnapshot: Bool, revision: Int?, changeTag: String?) {
        zoneExists ? (true, revision, changeTag) : (true, nil, nil)
    }

    func replayIssues(_ ids: [String]) -> [String] {
        issueIDs.formUnion(ids)
        return issueIDs.sorted { Array($0.utf8).lexicographicallyPrecedes(Array($1.utf8)) }
    }
}

final class ProtocolConformanceTests: XCTestCase {
    private func fixture() throws -> [String: Any] {
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let data = try Data(contentsOf: root.appendingPathComponent("protocol-fixtures/progress-v1.json"))
        return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    private func string(_ object: [String: Any], _ key: String) throws -> String {
        try XCTUnwrap(object[key] as? String, "missing \(key)")
    }

    private func integer(_ object: [String: Any], _ key: String) throws -> Int {
        try XCTUnwrap(object[key] as? Int, "missing \(key)")
    }

    private func semanticAssertEqual(_ expected: Any, _ actual: Any, path: String = "$") {
        switch (expected, actual) {
        case let (expected as [String: Any], actual as [String: Any]):
            XCTAssertEqual(Set(expected.keys), Set(actual.keys), path)
            for key in expected.keys {
                semanticAssertEqual(expected[key] as Any, actual[key] as Any, path: "\(path).\(key)")
            }
        case let (expected as [Any], actual as [Any]):
            XCTAssertEqual(expected.count, actual.count, path)
            for (index, values) in zip(expected.indices, zip(expected, actual)) {
                semanticAssertEqual(values.0, values.1, path: "\(path)[\(index)]")
            }
        case let (expected as NSNumber, actual as NSNumber):
            XCTAssertEqual(expected, actual, path)
        case let (expected as String, actual as String):
            XCTAssertEqual(expected, actual, path)
        case (_ as NSNull, _ as NSNull):
            break
        default:
            XCTFail("type mismatch at \(path): \(type(of: expected)) vs \(type(of: actual))")
        }
    }

    private func canonicalHash(_ object: [String: Any]) throws -> String {
        let data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .withoutEscapingSlashes])
        return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    func testVersionedFixtureUsesIntegerFixedPointAndEpochMilliseconds() throws {
        let corpus = try fixture()
        XCTAssertEqual(try integer(corpus, "version"), 1)
        let representations = try XCTUnwrap(corpus["representations"] as? [String: Any])
        XCTAssertTrue(try string(representations, "time").contains("epoch milliseconds"))
        XCTAssertTrue(try string(representations, "fixed_point").contains("Decimal scores"))
        XCTAssertTrue(try string(representations, "order").contains("conditional snapshot/change-tag"))
        let negotiation = try XCTUnwrap(corpus["version_negotiation"] as? [String: Any])
        XCTAssertEqual(try XCTUnwrap(negotiation["supported_versions"] as? [Int]), [1])
    }

    func testFakeCloudAndLocalStoreMatchFixtureBeforeCanonicalHash() async throws {
        let corpus = try fixture()
        let parity = try XCTUnwrap(corpus["parity_case"] as? [String: Any])
        let rawOperations = try XCTUnwrap(parity["operations"] as? [[String: Any]])
        let cas = try XCTUnwrap(corpus["cas_case"] as? [String: Any])
        let fakeCloud = FixtureCASCloud()
        let attempts = try XCTUnwrap(cas["attempts"] as? [[String: Any]])
        var assignedRevisions: [String: Int] = [:]

        for attempt in attempts {
            let operationIDs = try XCTUnwrap(attempt["operation_ids"] as? [String])
            let expectedRevision = try integer(attempt, "expected_revision")
            let expectedChangeTag = attempt["expected_change_tag"] as? String
            let expectedOutcome = try XCTUnwrap(attempt["outcome"] as? [String: Any])
            if try string(expectedOutcome, "status") == "rebase_required" {
                do {
                    _ = try fakeCloud.commit(
                        operationIDs: operationIDs,
                        expectedRevision: expectedRevision,
                        expectedChangeTag: expectedChangeTag
                    )
                    XCTFail("stale CAS unexpectedly succeeded")
                } catch let conflict as FixtureCASConflict {
                    let fullFetch = try XCTUnwrap(expectedOutcome["full_fetch"] as? [String: Any])
                    semanticAssertEqual(fullFetch, [
                        "revision": conflict.revision,
                        "change_tag": conflict.changeTag as Any
                    ])
                }
                continue
            }
            let result = try fakeCloud.commit(
                operationIDs: operationIDs,
                expectedRevision: expectedRevision,
                expectedChangeTag: expectedChangeTag
            )
            semanticAssertEqual(expectedOutcome, [
                "status": "applied",
                "revision": result.revision,
                "change_tag": result.changeTag,
                "assigned_revisions": result.assignedRevisions as [String: Any]
            ])
            assignedRevisions.merge(result.assignedRevisions) { _, new in new }
        }

        let batch = try XCTUnwrap(cas["batch"] as? [String: Any])
        let batchIDs = try XCTUnwrap(batch["operation_ids"] as? [String])
        XCTAssertLessThanOrEqual(
            try integer(batch, "snapshot_records") + batchIDs.count,
            try integer(batch, "maximum_atomic_records")
        )
        let batchResult = try fakeCloud.commit(
            operationIDs: batchIDs,
            expectedRevision: try integer(batch, "expected_revision"),
            expectedChangeTag: batch["expected_change_tag"] as? String
        )
        let expectedBatchOutcome = try XCTUnwrap(batch["outcome"] as? [String: Any])
        semanticAssertEqual(expectedBatchOutcome, [
            "status": "applied",
            "revision": batchResult.revision,
            "change_tag": batchResult.changeTag,
            "assigned_revisions": batchResult.assignedRevisions as [String: Any]
        ])

        let emptyZone = try XCTUnwrap(cas["empty_zone_rebase"] as? [String: Any])
        fakeCloud.deleteZone()
        let fullFetch = fakeCloud.fullFetch()
        semanticAssertEqual(
            try XCTUnwrap(emptyZone["full_fetch"] as? [String: Any]),
            ["is_full_snapshot": fullFetch.isFullSnapshot, "snapshot": NSNull()]
        )
        let retainedIDs = try XCTUnwrap(emptyZone["retained_operation_ids"] as? [String])
        let conditionalCreate = try XCTUnwrap(emptyZone["conditional_create"] as? [String: Any])
        let resetResult = try fakeCloud.commit(
            operationIDs: retainedIDs,
            expectedRevision: try integer(conditionalCreate, "expected_revision"),
            expectedChangeTag: conditionalCreate["expected_change_tag"] as? String
        )
        semanticAssertEqual(
            try XCTUnwrap(conditionalCreate["outcome"] as? [String: Any]),
            [
                "status": "applied",
                "revision": resetResult.revision,
                "change_tag": resetResult.changeTag,
                "assigned_revisions": resetResult.assignedRevisions as [String: Any]
            ]
        )
        let issueReplay = try XCTUnwrap(emptyZone["issue_replay"] as? [String: Any])
        let retainedIssues = try XCTUnwrap(issueReplay["issue_ids"] as? [String])
        semanticAssertEqual(issueReplay, [
            "status": "applied",
            "issue_ids": fakeCloud.replayIssues(retainedIssues) as [Any]
        ])
        XCTAssertEqual(fakeCloud.replayIssues(retainedIssues), retainedIssues)

        let operations: [ProgressMergeOperation] = try rawOperations.map { raw in
            let sessionRaw = try XCTUnwrap(raw["session"] as? [String: Any])
            let answers = try XCTUnwrap(sessionRaw["answers"] as? [[String: Any]]).map { answer in
                SessionAnswer(
                    courseID: try string(answer, "course_id"),
                    packID: try string(answer, "pack_id"),
                    questionID: try string(answer, "question_id"),
                    correct: try XCTUnwrap(answer["correct"] as? Bool)
                )
            }
            let createdAt = Date(timeIntervalSince1970: Double(try integer(raw, "created_at_ms")) / 1_000)
            let operationID = try string(raw, "operation_id")
            let serverRevision = try XCTUnwrap(assignedRevisions[operationID])
            return ProgressMergeOperation(
                operationID: operationID,
                baseRevision: serverRevision - 1,
                baseOperationID: serverRevision == 1 ? nil : "op-a",
                serverRevision: serverRevision,
                createdAt: createdAt,
                serverRecordedAt: Date(timeIntervalSince1970: Double(try integer(raw, "server_recorded_at_ms")) / 1_000),
                session: SessionDetail(
                    sessionID: try string(sessionRaw, "session_id"),
                    completedAt: Date(timeIntervalSince1970: Double(try integer(sessionRaw, "completed_at_ms")) / 1_000),
                    answers: answers
                )
            )
        }

        // This is the local reduction after the fake CloudKit CAS assigned
        // revisions. Input order and timestamps cannot alter the result.
        let trustedNow = Date(timeIntervalSince1970: 1_800_000_010)
        let snapshot = try ProgressMergeEngine.merge(
            operations,
            into: .empty(actorID: "conformance", createdAt: Date(timeIntervalSince1970: 0), operationID: "baseline"),
            requireBaseRevision: true,
            now: trustedNow
        )
        let file = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("protocol-conformance-\(UUID().uuidString).json")
        defer { try? FileManager.default.removeItem(at: file) }
        let store = LocalProgressStore(fileURL: file)
        try await store.write(snapshot.snapshot.envelope)
        let localRead = try await store.read()
        let local = try XCTUnwrap(localRead)

        let expected = try XCTUnwrap(parity["expected"] as? [String: Any])
        let ordered = ProgressMergeEngine.ordered(operations)
        let actual: [String: Any] = [
            "schema_version": local.schemaVersion,
            "revision": local.documentRevision,
            "sessions_total": local.aggregate.sessionsTotal,
            "answered": local.aggregate.answered,
            "correct": local.aggregate.correct,
            "score_fixed": "0.666667",
            "operation_order": ordered.map(\.operationID),
            "session_ids": local.sessionDetails.map(\.id),
            "answers": local.sessionDetails.flatMap(\.answers).map {
                [$0.identity.courseID, $0.identity.packID, $0.identity.questionID, $0.correct] as [Any]
            },
            "times_ms": ordered.map { Int(($0.createdAt.timeIntervalSince1970 * 1_000).rounded()) }
        ]
        semanticAssertEqual(expected, actual)
        XCTAssertEqual(try canonicalHash(actual), try string(parity, "canonical_sha256"))
        XCTAssertEqual(
            try snapshot.snapshot.canonicalEvidenceHash(),
            try string(parity, "swift_canonical_sha256")
        )
    }

    func testRecoveryTieBreakSkewRetentionCompactionAndRebaseAreExplicit() throws {
        let epoch = Date(timeIntervalSince1970: 1_800_000_000)
        let session = SessionDetail(sessionID: "equal", completedAt: epoch, answers: [])
        let z = ProgressMergeOperation(operationID: "z", baseRevision: 0, serverRevision: 1, createdAt: epoch.addingTimeInterval(-10_000), session: session)
        let a = ProgressMergeOperation(operationID: "a", baseRevision: 0, serverRevision: 1, createdAt: epoch.addingTimeInterval(10_000), session: session)
        // Equal revisions are recovery evidence only. They are never a way
        // to make two competing snapshot CAS writes succeed.
        let equal = try ProgressMergeEngine.merge([z, a], into: .empty(actorID: "conformance"))
        XCTAssertEqual(equal.appliedOperationIDs, ["a", "z"])
        XCTAssertTrue(equal.rebased)

        let many = (1...4_097).map { index in
            ProgressMergeOperation(operationID: String(format: "op-%05d", index), baseRevision: 0, serverRevision: index, createdAt: epoch, serverRecordedAt: epoch, session: SessionDetail(sessionID: "s-\(index)", completedAt: epoch, answers: []))
        }
        let source = try ProgressMergeSnapshot(envelope: ProgressEnvelope(actorID: "conformance"), operations: many)
        let plan = try ProgressCompactor.plan(snapshot: source, now: epoch)
        XCTAssertEqual(plan.snapshot.operations.count, 4_096)
        XCTAssertEqual(plan.deletedOperationIDs.count, 1)
        XCTAssertTrue(plan.deleteBatches.allSatisfy { $0.count < 250 })

        let retentionBoundary = ProgressMergeOperation(
            operationID: "retention-boundary",
            baseRevision: 0,
            serverRevision: 1,
            createdAt: epoch.addingTimeInterval(10_000),
            serverRecordedAt: epoch.addingTimeInterval(-31 * 86_400),
            session: SessionDetail(sessionID: "old", completedAt: epoch, answers: [])
        )
        let oldSnapshot = try ProgressMergeSnapshot(envelope: ProgressEnvelope(actorID: "conformance"), operations: [retentionBoundary])
        let oldPlan = try ProgressCompactor.plan(snapshot: oldSnapshot, now: epoch)
        XCTAssertEqual(oldPlan.deletedOperationIDs, ["retention-boundary"])

        let sessions = (1...201).map { index in
            ProgressMergeOperation(
                operationID: String(format: "session-%03d", index),
                baseRevision: max(0, index - 1),
                baseOperationID: index == 1 ? nil : String(format: "session-%03d", index - 1),
                serverRevision: index,
                createdAt: epoch,
                serverRecordedAt: epoch,
                session: SessionDetail(sessionID: "detail-\(index)", completedAt: epoch, answers: [])
            )
        }
        let retained = try ProgressMergeEngine.merge(
            sessions,
            into: .empty(actorID: "conformance", createdAt: epoch, operationID: "baseline"),
            now: epoch
        ).snapshot.envelope
        XCTAssertEqual(retained.sessionDetails.count, 200)
        XCTAssertEqual(retained.aggregate.sessionsTotal, 201)
        XCTAssertEqual(retained.sessionDetails.first?.id, "detail-2")
    }

    func testLocalStoreRefusesOversizeEnvelopeWithoutWriting() async throws {
        let file = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("protocol-size-\(UUID().uuidString).json")
        defer { try? FileManager.default.removeItem(at: file) }
        let store = LocalProgressStore(fileURL: file, maximumEncodedSize: 1)
        do {
            try await store.write(ProgressEnvelope(actorID: "conformance"))
            XCTFail("oversize envelope was accepted")
        } catch {
            XCTAssertEqual(error as? LocalProgressStoreError, .encodedSizeRefused)
        }
        let persisted = try await store.read()
        XCTAssertNil(persisted)

        let versionFile = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("protocol-version-\(UUID().uuidString).json")
        defer { try? FileManager.default.removeItem(at: versionFile) }
        let versionStore = LocalProgressStore(fileURL: versionFile)
        let incompatible = ProgressEnvelope(schemaVersion: 2, actorID: "conformance")
        do {
            try await versionStore.write(incompatible)
            XCTFail("incompatible schema was accepted")
        } catch {
            XCTAssertEqual(error as? LocalProgressStoreError, .corruptState)
        }
    }
}
