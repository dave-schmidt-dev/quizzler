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

    private func reviewEvent() -> QuestionReviewEvent {
        QuestionReviewEvent(
            operationID: "operation-1",
            ordinal: 0,
            identity: QuestionIdentity(courseID: "course", packID: "pack", questionID: "question"),
            eventTime: date,
            outcome: .correct,
            priorLevel: 1,
            resultingLevel: 2,
            resultingDueAt: date.addingTimeInterval(86_400),
            serverRevision: 7
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

    func testVersionTwoMaximumOperationAndSnapshotRoundTripWithoutDowngrade() throws {
        let operation = ProgressOperation(
            operationID: "limit-3", createdAt: date, status: .applied,
            kind: .setMaximumLeitnerLevel, maximumLeitnerLevel: 3
        )
        let mappedOperation = try CloudKitMapping.operationRecord(operation)
        XCTAssertEqual(mappedOperation.fields["schema_version"], .integer(2))
        XCTAssertEqual(try CloudKitMapping.operation(from: mappedOperation), operation)

        let envelope = ProgressEnvelope(
            schemaVersion: 2, documentRevision: 1, actorID: "device-a",
            operationID: operation.id, maximumLeitnerLevel: 3, operations: [operation]
        )
        let mappedSnapshot = try CloudKitMapping.snapshotRecord(envelope)
        XCTAssertEqual(mappedSnapshot.fields["schema_version"], .integer(2))
        XCTAssertEqual(try CloudKitMapping.snapshot(from: mappedSnapshot), envelope)

        // The cap may return to its default and the cap operation may be
        // compacted, but the upgraded envelope must stay version two.
        let reset = ProgressEnvelope(schemaVersion: 2, documentRevision: 2, actorID: "device-a")
        let resetRecord = try CloudKitMapping.snapshotRecord(reset)
        XCTAssertEqual(resetRecord.fields["schema_version"], .integer(2))
        XCTAssertEqual(try CloudKitMapping.snapshot(from: resetRecord), reset)
    }

    func testVersionOneSnapshotCannotCarryAChangedMaximum() throws {
        let malformed = ProgressEnvelope(schemaVersion: 1, actorID: "device-a", maximumLeitnerLevel: 3)
        XCTAssertThrowsError(try CloudKitMapping.snapshotRecord(malformed)) { error in
            XCTAssertEqual(error as? CloudKitMappingError, .payloadMismatch)
        }
    }

    func testOptionalSelectedResponseSupportsLegacyIssueRecord() throws {
        let mapped = try CloudKitMapping.issueRecord(try issue(selectedResponse: nil))
        XCTAssertNil(mapped.fields["selected_response"])
        XCTAssertEqual(try CloudKitMapping.issue(from: mapped).selectedResponse, nil)
    }

    func testReviewEventRoundTripUsesV2ImmutableNameAndFullIdentityQueryKey() throws {
        let event = reviewEvent()
        let mapped = try CloudKitMapping.reviewEventRecord(event)

        XCTAssertEqual(mapped.kind, .reviewEvent)
        XCTAssertEqual(mapped.recordName, "QuestionReviewEvent/operation-1:0")
        XCTAssertEqual(mapped.fields["schema_version"], .integer(2))
        XCTAssertEqual(mapped.fields["question_key"], .string("6:course|4:pack|8:question"))
        XCTAssertEqual(try CloudKitMapping.reviewEvent(from: mapped), event)
        XCTAssertEqual(try CloudKitMapping.decode(mapped), .reviewEvent(event))
    }

    func testDerivedEventsKeepAnswerOrdinalAndCapturedTimeAcrossMapping() throws {
        let identity = QuestionIdentity(courseID: "course", packID: "pack", questionID: "question")
        let answers = [
            SessionAnswer(identity: identity, correct: true, answeredAt: date),
            SessionAnswer(identity: identity, correct: false, answeredAt: date.addingTimeInterval(5))
        ]
        var operation = ProgressOperation(
            operationID: "two-answers", createdAt: date, status: .applied,
            session: SessionDetail(sessionID: "two", completedAt: date.addingTimeInterval(20), answers: answers)
        )
        operation.serverRevision = 9
        let baseline = ProgressEnvelope(schemaVersion: 1, actorID: "device-a")
        let events = ProgressEnvelope.reviewEvents(for: operation, from: baseline)
        let mapped = try events.map(CloudKitMapping.reviewEventRecord)

        XCTAssertEqual(mapped.map(\.recordName), ["QuestionReviewEvent/two-answers:0", "QuestionReviewEvent/two-answers:1"])
        XCTAssertEqual(try mapped.map(CloudKitMapping.reviewEvent(from:)), events)
        XCTAssertEqual(events.map(\.eventTime), [date, date.addingTimeInterval(5)])
        XCTAssertEqual(events.map(\.priorLevel), [1, 2])
        XCTAssertEqual(events.map(\.resultingLevel), [2, 1])
        XCTAssertEqual(mapped.map { $0.fields["server_revision"] }, [.integer(9), .integer(9)])
    }

    func testReviewEventRejectsMismatchedNameIdentityAndPayload() throws {
        let mapped = try CloudKitMapping.reviewEventRecord(reviewEvent())

        XCTAssertThrowsError(try CloudKitMapping.reviewEvent(from: CloudKitMappedRecord(
            kind: .reviewEvent,
            recordName: "QuestionReviewEvent/not-the-event",
            fields: mapped.fields
        )))

        var mismatchedIdentity = mapped.fields
        mismatchedIdentity["question_id"] = .string("other")
        XCTAssertThrowsError(try CloudKitMapping.reviewEvent(from: CloudKitMappedRecord(
            kind: .reviewEvent,
            recordName: mapped.recordName,
            fields: mismatchedIdentity
        )))

        var malformed = mapped.fields
        malformed["server_revision"] = .integer(0)
        XCTAssertThrowsError(try CloudKitMapping.reviewEvent(from: CloudKitMappedRecord(
            kind: .reviewEvent,
            recordName: mapped.recordName,
            fields: malformed
        )))

        var unsupportedSchema = mapped.fields
        unsupportedSchema["schema_version"] = .integer(3)
        XCTAssertThrowsError(try CloudKitMapping.reviewEvent(from: CloudKitMappedRecord(
            kind: .reviewEvent,
            recordName: mapped.recordName,
            fields: unsupportedSchema
        ))) { error in
            XCTAssertEqual(error as? CloudKitMappingError, .incompatibleVersion(3))
        }
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
        incompatibleFields["schema_version"] = .integer(3)
        let incompatible = try CloudKitMappedRecord(kind: mapped.kind, recordName: mapped.recordName, fields: incompatibleFields)
        XCTAssertThrowsError(try CloudKitMapping.operation(from: incompatible)) { error in
            XCTAssertEqual(error as? CloudKitMappingError, .incompatibleVersion(3))
        }
    }

    func testCloudKitContractRejectsUnsafeRecordNames() {
        XCTAssertThrowsError(try CloudKitContract.recordName(for: .operation, identifier: "a/b"))
        XCTAssertEqual(CloudKitContract.zoneName, "QuizzlerProgress-v1")
        XCTAssertEqual(CloudKitRecordKind.allCases.map(\.rawValue), ["ProgressOperation", "ProgressSnapshot", "QuestionIssue", "QuestionReviewEvent"])
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
        let record = try CloudKitMapping.snapshotRecord(queueOverflow)
        let rawPayload = try XCTUnwrap(record.fields["payload"])
        guard case let .data(payloadData) = rawPayload else {
            return XCTFail("payload must be data")
        }
        let decodedEnvelope = try JSONDecoder().decode(ProgressEnvelope.self, from: payloadData)
        XCTAssertTrue(decodedEnvelope.issues.isEmpty)

        let mappedEnvelope = try CloudKitMapping.snapshot(from: record)
        XCTAssertTrue(mappedEnvelope.issues.isEmpty)
    }
}
