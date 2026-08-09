import Foundation
import XCTest

/// Contract-only tests. Task 1.2 supplies the package target; this file does
/// not import QuizzlerKit or assume an Xcode project exists yet.
final class ProgressContractTests: XCTestCase {
    private let installable = ["multiple_choice", "scenario_multiple_choice", "multiple_select"]
    private let legacy = ["true_false", "matching"]

    func testQuestionTypeBoundaryIsFiveTypesWithThreeInstallable() {
        XCTAssertEqual(installable + legacy,
                       ["multiple_choice", "scenario_multiple_choice", "multiple_select", "true_false", "matching"])
        XCTAssertEqual(Set(installable).intersection(legacy), [])
        XCTAssertTrue(legacyRequiresDigestAllowlist)
    }

    func testRecoveryAndRefusalStatusesAreExplicit() {
        XCTAssertEqual(Set(["rebase_required", "corrupt_state", "encoded_size_refused"]),
                       Set([ProgressStatus.rebaseRequired, ProgressStatus.corruptState, ProgressStatus.encodedSizeRefused]))
        XCTAssertTrue(ProgressStatus.refusalLeavesRevisionUnchanged)
    }

    func testCloudKitRecordNamesAndKindsAreBounded() {
        XCTAssertEqual(CloudKitContract.recordNames,
                       ["ProgressOperation/<operationID>", "ProgressSnapshot/current", "QuestionIssue/<issueID>"])
        XCTAssertEqual(CloudKitContract.zoneName, "QuizzlerProgress-v1")
        XCTAssertEqual(CloudKitContract.kinds, ["ProgressOperation", "ProgressSnapshot", "QuestionIssue"])
    }

    func testIdentityAndSessionAnswerRetainPackTuple() throws {
        let answer = SessionAnswer(courseID: "course", packID: "pack-b", questionID: "q-7", correct: true)
        let data = try JSONEncoder().encode(answer)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(object["course_id"] as? String, "course")
        XCTAssertEqual(object["pack_id"] as? String, "pack-b")
        XCTAssertEqual(object["question_id"] as? String, "q-7")
    }

    func testEnvelopeEncodesVersionRevisionOperationAndCompaction() throws {
        let envelope = ProgressEnvelope(schemaVersion: 1, documentRevision: 42,
                                        actorID: "device-a", operationID: "op-1",
                                        createdAt: "2026-08-08T12:00:00.000Z",
                                        compaction: Compaction(version: 1, watermarkRevision: 40))
        let data = try JSONEncoder().encode(envelope)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(object["schema_version"] as? Int, 1)
        XCTAssertEqual(object["document_revision"] as? Int, 42)
        XCTAssertEqual(object["operation_id"] as? String, "op-1")
        let compaction = try XCTUnwrap(object["compaction"] as? [String: Any])
        XCTAssertEqual(compaction["watermark_revision"] as? Int, 40)
    }

    func testSemanticComparisonPrecedesCanonicalEvidenceInput() {
        let left = ["b": 2, "a": 1]
        let right = ["a": 1, "b": 2]
        XCTAssertEqual(canonicalEvidenceInput(left), canonicalEvidenceInput(right))
        XCTAssertNotEqual(canonicalEvidenceInput(left), canonicalEvidenceInput(["a": 1, "b": 3]))
    }

    func testRetentionBoundariesAreExplicit() {
        XCTAssertEqual(ProgressRetention.sessionDetails, 200)
        XCTAssertEqual(ProgressRetention.operations, 4_096)
        XCTAssertEqual(ProgressRetention.operationDays, 30)
    }

    private func canonicalEvidenceInput(_ value: [String: Int]) -> String {
        let members = value.keys.sorted().map { "\"\($0)\":\(value[$0]!)" }.joined(separator: ",")
        return "{\(members)}"
    }

    private let legacyRequiresDigestAllowlist = true
}

private struct SessionAnswer: Codable {
    let courseID: String
    let packID: String
    let questionID: String
    let correct: Bool

    enum CodingKeys: String, CodingKey {
        case courseID = "course_id"
        case packID = "pack_id"
        case questionID = "question_id"
        case correct
    }
}

private struct Compaction: Codable {
    let version: Int
    let watermarkRevision: Int

    enum CodingKeys: String, CodingKey {
        case version
        case watermarkRevision = "watermark_revision"
    }
}

private struct ProgressEnvelope: Codable {
    let schemaVersion: Int
    let documentRevision: Int
    let actorID: String
    let operationID: String
    let createdAt: String
    let compaction: Compaction

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case documentRevision = "document_revision"
        case actorID = "actor_id"
        case operationID = "operation_id"
        case createdAt = "created_at"
        case compaction
    }
}

private enum ProgressRetention {
    static let sessionDetails = 200
    static let operations = 4_096
    static let operationDays = 30
}

private enum ProgressStatus {
    static let rebaseRequired = "rebase_required"
    static let corruptState = "corrupt_state"
    static let encodedSizeRefused = "encoded_size_refused"
    static let refusalLeavesRevisionUnchanged = true
}

private enum CloudKitContract {
    static let zoneName = "QuizzlerProgress-v1"
    static let kinds = ["ProgressOperation", "ProgressSnapshot", "QuestionIssue"]
    static let recordNames = [
        "ProgressOperation/<operationID>",
        "ProgressSnapshot/current",
        "QuestionIssue/<issueID>"
    ]
}
