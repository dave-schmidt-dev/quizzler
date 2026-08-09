import XCTest
@testable import QuizzlerKit

final class QuestionIdentityTests: XCTestCase {
    func testSameQuestionIDInDifferentPacksIsDistinct() {
        let a = QuestionIdentity(courseID: "course", packID: "a", questionID: "q1")
        let b = QuestionIdentity(courseID: "course", packID: "b", questionID: "q1")
        XCTAssertNotEqual(a, b)
        XCTAssertEqual(a.description, "course::a::q1")
    }
}
