import Foundation
import XCTest
@testable import QuizzlerKit

final class QuestionIssueTests: XCTestCase {
    private func makeIssue(description: String = "The keyed answer is inconsistent with the explanation.") throws -> QuestionIssue {
        try QuestionIssue(
            issueID: "issue-018f2c0e",
            courseID: "itn260",
            packID: "final-review-ch9-15",
            questionID: "r4q13",
            questionType: .multipleChoice,
            appVersion: "1.0.0",
            build: "100",
            selectedResponse: "B",
            description: description
        )
    }

    func testEncodingUsesExactSchemaV1AndRoundTrips() throws {
        let issue = try makeIssue()
        let data = try JSONEncoder().encode(issue)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])

        XCTAssertEqual(Set(object.keys), [
            "schema_version", "issue_id", "course_id", "pack_id", "question_id",
            "question_type", "app_version", "build", "selected_response", "description"
        ])
        XCTAssertEqual(object["schema_version"] as? Int, 1)
        XCTAssertEqual(object["question_type"] as? String, "multiple_choice")
        XCTAssertEqual(try JSONDecoder().decode(QuestionIssue.self, from: data), issue)
    }

    func testPayloadExcludesQuestionAndAnswerContent() throws {
        let issue = try makeIssue(description: "The keyed response appears inconsistent.")
        let json = String(decoding: try JSONEncoder().encode(issue), as: UTF8.self)

        XCTAssertFalse(json.contains("prompt"))
        XCTAssertFalse(json.contains("options"))
        XCTAssertFalse(json.contains("answer"))
        XCTAssertFalse(json.contains("explanation"))
        XCTAssertFalse(json.contains("question_text"))
        XCTAssertFalse(json.contains("pack_contents"))
    }

    func testInputIsTrimmedAndSelectedResponseIsOptional() throws {
        let issue = try QuestionIssue(
            issueID: " issue-1 ", courseID: " course ", packID: " pack ", questionID: " q1 ",
            questionType: .trueFalse, appVersion: " 1.0 ", build: " 2 ",
            selectedResponse: " B ", description: "  needs review  "
        )
        XCTAssertEqual(issue.issueID, " issue-1 ")
        XCTAssertEqual(issue.selectedResponse, "B")
        XCTAssertEqual(issue.description, "needs review")

        let withoutSelection = try QuestionIssue(
            courseID: "course", packID: "pack", questionID: "q1", questionType: .matching,
            appVersion: "1.0", build: "2", description: "needs review"
        )
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(withoutSelection)) as? [String: Any])
        XCTAssertNil(object["selected_response"])
    }

    func testInvalidInputIsRejected() {
        XCTAssertThrowsError(try QuestionIssue(courseID: "course", packID: "pack", questionID: "q1", questionType: .multipleChoice, appVersion: "1.0", build: "1", description: "   ")) { error in
            XCTAssertEqual(error as? QuestionIssueValidationError, .blankField("description"))
        }
        XCTAssertThrowsError(try QuestionIssue(courseID: "course", packID: "pack", questionID: "q1", questionType: .multipleChoice, appVersion: "1.0", build: "1", description: String(repeating: "x", count: QuestionIssue.maxDescriptionLength + 1)))
        XCTAssertThrowsError(try QuestionIssue(courseID: "course", packID: "pack", questionID: "q1", questionType: .multipleChoice, appVersion: "1.0", build: "1", selectedResponse: String(repeating: "x", count: QuestionIssue.maxSelectedResponseLength + 1), description: "valid"))
    }

    func testDecoderRejectsWrongVersionAndUnknownField() throws {
        let valid = try makeIssue()
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(valid)) as? [String: Any])
        object["schema_version"] = 2
        XCTAssertThrowsError(try JSONDecoder().decode(QuestionIssue.self, from: JSONSerialization.data(withJSONObject: object))) { error in
            XCTAssertEqual(error as? QuestionIssueValidationError, .unsupportedSchemaVersion(2))
        }

        object["schema_version"] = 1
        object["question_text"] = "must never be accepted"
        XCTAssertThrowsError(try JSONDecoder().decode(QuestionIssue.self, from: JSONSerialization.data(withJSONObject: object))) { error in
            XCTAssertEqual(error as? QuestionIssueValidationError, .unknownField("question_text"))
        }
    }
}
