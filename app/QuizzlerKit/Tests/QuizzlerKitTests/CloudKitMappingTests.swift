import Foundation
import XCTest
@testable import QuizzlerKit

final class CloudKitMappingTests: XCTestCase {
    private let date = Date(timeIntervalSince1970: 1_723_123_456.789)

    private func session() -> SessionDetail {
        SessionDetail(
            sessionID: "session-1",
            completedAt: date,
            answers: [SessionAnswer(courseID: "course", packID: "pack", questionID: "question", correct: true)]
        )
    }

    private func issue(selectedResponse: String? = "B") throws -> QuestionIssue {
        try QuestionIssue(
            issueID: "issue-1",
            courseID: "course",
            packID: "pack",
            questionID: "question",
            questionType: .multipleChoice,
            appVersion: "1.0.0",
            build: "100",
            selectedResponse: selectedResponse,
            description: "The explanation is inconsistent."
        )
    }

    func testOperationSnapshotAndIssueRoundTrip() throws {
        let operation = ProgressOperation(
            operationID: "operation-1",
            createdAt: date,
            status: .applied,
            session: session()
        )
        let envelope = ProgressEnvelope(
            documentRevision: 1,
            actorID: "device-a",
            operationID: operation.id,
            createdAt: date,
            sessionDetails: [session()],
            aggregate: AggregateSnapshot(sessionsTotal: 1, answered: 1, correct: 1),
            operations: [operation]
        )

        let mappedOperation = try CloudKitMapping.operationRecord(operation)
        let mappedSnapshot = try CloudKitMapping.snapshotRecord(envelope)
        let mappedIssue = try CloudKitMapping.issueRecord(try issue())

        XCTAssertEqual(try CloudKitMapping.operation(from: mappedOperation), operation)
        XCTAssertEqual(try CloudKitMapping.snapshot(from: mappedSnapshot), envelope)
        XCTAssertEqual(try CloudKitMapping.issue(from: mappedIssue), try issue())
        XCTAssertEqual(mappedOperation.recordName, "ProgressOperation/operation-1")
        XCTAssertEqual(mappedSnapshot.recordName, "ProgressSnapshot/current")
        XCTAssertEqual(mappedIssue.recordName, "QuestionIssue/issue-1")
    }

    func testOptionalSelectedResponseSupportsLegacyIssueRecord() throws {
        let mapped = try CloudKitMapping.issueRecord(try issue(selectedResponse: nil))
        XCTAssertNil(mapped.fields["selected_response"])
        XCTAssertEqual(try CloudKitMapping.issue(from: mapped).selectedResponse, nil)
    }

    func testOperationAcceptsOptionalAuthoritativeServerRevision() throws {
        let operation = ProgressOperation(operationID: "operation-1", createdAt: date, status: .applied, session: session())
        let mapped = try CloudKitMapping.operationRecord(operation)
        var fields = mapped.fields
        fields["server_revision"] = .integer(42)
        let decoded = try CloudKitMapping.operation(from: CloudKitMappedRecord(
            kind: mapped.kind,
            recordName: mapped.recordName,
            fields: fields
        ))
        var expected = operation
        expected.serverRevision = 42
        XCTAssertEqual(decoded, expected)
    }

    func testUnknownFieldIsRejectedBeforePayloadDecode() throws {
        let operation = ProgressOperation(operationID: "operation-1", createdAt: date, status: .applied, session: session())
        let mapped = try CloudKitMapping.operationRecord(operation)
        var fields = mapped.fields
        fields["question_text"] = .string("must never cross the boundary")
        let malformed = try CloudKitMappedRecord(kind: mapped.kind, recordName: mapped.recordName, fields: fields)

        XCTAssertThrowsError(try CloudKitMapping.operation(from: malformed)) { error in
            XCTAssertEqual(error as? CloudKitMappingError, .unknownField("question_text"))
        }
    }

    func testMalformedAndIncompatibleRecordsFailClosed() throws {
        let operation = ProgressOperation(operationID: "operation-1", createdAt: date, status: .applied, session: session())
        let mapped = try CloudKitMapping.operationRecord(operation)

        var malformedFields = mapped.fields
        malformedFields["payload"] = .string("not-data")
        let malformed = try CloudKitMappedRecord(kind: mapped.kind, recordName: mapped.recordName, fields: malformedFields)
        XCTAssertThrowsError(try CloudKitMapping.operation(from: malformed))

        var incompatibleFields = mapped.fields
        incompatibleFields["schema_version"] = .integer(2)
        let incompatible = try CloudKitMappedRecord(kind: mapped.kind, recordName: mapped.recordName, fields: incompatibleFields)
        XCTAssertThrowsError(try CloudKitMapping.operation(from: incompatible)) { error in
            XCTAssertEqual(error as? CloudKitMappingError, .incompatibleVersion(2))
        }
    }

    func testCloudKitContractRejectsUnsafeRecordNames() {
        XCTAssertThrowsError(try CloudKitContract.recordName(for: .operation, identifier: "a/b"))
        XCTAssertEqual(CloudKitContract.zoneName, "QuizzlerProgress-v1")
        XCTAssertEqual(CloudKitRecordKind.allCases.map(\.rawValue), ["ProgressOperation", "ProgressSnapshot", "QuestionIssue"])
    }

    func testCloudKitRecordZoneChangeBatchUsesAppleServerLimit() {
        XCTAssertEqual(CloudKitContract.maximumRecordsPerBatch, 250)
    }

    func testSnapshotRefusesOversizedPayloadAndIssueQueue() throws {
        let oversizedAnswer = SessionAnswer(
            courseID: String(repeating: "c", count: 300_000),
            packID: String(repeating: "p", count: 300_000),
            questionID: String(repeating: "q", count: 300_000),
            correct: true
        )
        let oversized = ProgressEnvelope(
            actorID: "device-a",
            sessionDetails: [SessionDetail(sessionID: "large", completedAt: date, answers: [oversizedAnswer])]
        )
        XCTAssertThrowsError(try CloudKitMapping.snapshotRecord(oversized)) { error in
            XCTAssertEqual(error as? CloudKitMappingError, .encodedSizeRefused)
        }

        let issues = try (0...CloudKitContract.maximumQueuedIssues).map { index in
            try QuestionIssue(
                issueID: "issue-\(index)",
                courseID: "course",
                packID: "pack",
                questionID: "question-\(index)",
                questionType: .multipleChoice,
                appVersion: "1.0.0",
                build: "100",
                description: "valid"
            )
        }
        let queueOverflow = ProgressEnvelope(actorID: "device-a", issues: issues)
        XCTAssertThrowsError(try CloudKitMapping.snapshotRecord(queueOverflow)) { error in
            XCTAssertEqual(error as? CloudKitMappingError, .issueQueueLimitExceeded)
        }
    }
}
