import XCTest
@testable import QuizzleriOS
import QuizzlerKit

final class QuestionRendererSnapshotTests: XCTestCase {
    func testRendererFixtureInventoryIncludesAccessibilityBoundaries() {
        XCTAssertEqual(SeededStudyData.questions.count, 5)
        XCTAssertTrue(SeededStudyData.questions.allSatisfy { !$0.prompt.isEmpty && !$0.explanation.isEmpty })
        XCTAssertTrue(SeededStudyData.questions.allSatisfy { !$0.qid.isEmpty })
        XCTAssertGreaterThanOrEqual(QuizzlerTheme.minimumTouchTarget, 44)
    }

    func testRendererFixturesHaveDistinctPackScopedQuestionIDs() {
        let qids = SeededStudyData.questions.map(\.qid)
        XCTAssertEqual(Set(qids).count, qids.count)
        XCTAssertTrue(qids.allSatisfy { $0.hasPrefix("\(SeededStudyData.packID)::") })
    }

    func testQuestionTypesRemainTheSupportedFive() {
        XCTAssertEqual(QuestionType.allCases.map(\.rawValue), [
            "multiple_choice", "scenario_multiple_choice", "multiple_select", "true_false", "matching"
        ])
    }
}
