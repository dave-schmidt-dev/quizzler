import XCTest
@testable import QuizzleriOS
import QuizzlerKit

final class QuestionRendererSnapshotTests: XCTestCase {
    func testRendererFixtureInventoryIncludesAccessibilityBoundaries() {
        XCTAssertEqual(SeededStudyData.questions.count, 5)
        XCTAssertTrue(SeededStudyData.questions.allSatisfy { !$0.prompt.isEmpty && !$0.explanation.isEmpty })
        XCTAssertGreaterThanOrEqual(QuizzlerTheme.minimumTouchTarget, 44)
    }

    func testQuestionTypesRemainTheSupportedFive() {
        XCTAssertEqual(QuestionType.allCases.map(\.rawValue), [
            "multiple_choice", "scenario_multiple_choice", "multiple_select", "true_false", "matching"
        ])
    }
}
