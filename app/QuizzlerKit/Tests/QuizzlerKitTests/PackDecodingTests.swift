import Foundation
import XCTest
@testable import QuizzlerKit

final class PackDecodingTests: XCTestCase {
    func testInstallablePackDecodesAndRejectsDuplicateIDs() throws {
        let data = try fixture(type: "multiple_choice", id: "q1")
        let manifest = try PackLoader().load(data: data)
        XCTAssertEqual(manifest.questions.count, 1)
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let question = try XCTUnwrap((object["questions"] as? [[String: Any]])?.first)
        object["questions"] = [question, question]
        XCTAssertThrowsError(try PackLoader().load(data: JSONSerialization.data(withJSONObject: object)))
    }

    func testUnknownTypeAndInvalidAnswerFailClosed() throws {
        XCTAssertThrowsError(try PackLoader().load(data: fixture(type: "ordering", id: "q1")))
        XCTAssertThrowsError(try PackLoader().load(data: fixture(type: "multiple_choice", id: "q1", answer: 9)))
    }

    func testLegacyTypeNeedsExactDigestAllowlist() throws {
        let data = try fixture(type: "true_false", id: "q1", trueFalse: true)
        XCTAssertThrowsError(try PackLoader().load(data: data))
        let digest = PackLoader.contentDigest(for: data)
        XCTAssertNoThrow(try PackLoader(legacyDigestAllowlist: [digest]).load(data: data))
        XCTAssertThrowsError(try PackLoader(legacyDigestAllowlist: ["*"]).load(data: data))
    }

    private func fixture(type: String, id: String, answer: Int = 0, trueFalse: Bool? = nil) throws -> Data {
        var q: [String: Any] = ["id": id, "type": type, "topic": "topic", "exam_area": "area", "difficulty": "easy", "prompt": "Prompt", "explanation": "Explanation"]
        if let trueFalse { q["answer"] = trueFalse } else { q["options"] = ["A", "B"]; q["answer"] = answer }
        return try JSONSerialization.data(withJSONObject: ["pack_id": "p", "subject": "s", "title": "t", "version": 1, "questions": [q]])
    }
}
