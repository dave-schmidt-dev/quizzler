import XCTest

final class QuizWorkflowUITests: XCTestCase {
    private let timeout: TimeInterval = 5

    func testTodayStartsReviewAndKeepsQuestionIdentityAndReportReachable() {
        let app = XCUIApplication()
        app.launch()

        XCTAssertTrue(app.staticTexts["TODAY · SECURITY+"].waitForExistence(timeout: timeout))
        let startReview = app.buttons["Start review"]
        XCTAssertTrue(startReview.waitForExistence(timeout: timeout))
        startReview.tap()

        XCTAssertTrue(app.staticTexts["Question ID sy0-701::q0042"].waitForExistence(timeout: timeout))
        XCTAssertTrue(app.buttons["Report"].waitForExistence(timeout: timeout))
        XCTAssertTrue(app.buttons["Check Answer"].waitForExistence(timeout: timeout))
    }
}
