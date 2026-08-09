import Foundation
import XCTest
@testable import QuizzlerKit

final class QuestionIssueTests: XCTestCase {
    func testIssueIsIdentityOnlyAndQueuesDurably() async throws {
        let identity = QuestionIdentity(courseID: "course", packID: "pack", questionID: "q-1")
        let issue = QuestionIssue(issueID: "issue-1", identity: identity, category: .incorrectAnswer, note: "answer key review")
        let data = try JSONEncoder().encode(issue)
        let json = String(decoding: data, as: UTF8.self)
        XCTAssertFalse(json.contains("question_text"))
        XCTAssertFalse(json.contains("pack_contents"))

        let repository = ProgressRepository(actorID: "device-a")
        _ = try await repository.queueIssue(issue)
        let snapshot = try await repository.snapshot()
        XCTAssertEqual(snapshot.issues, [issue])
    }
}
