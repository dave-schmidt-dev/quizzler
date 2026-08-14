import XCTest

@MainActor
final class QuizWorkflowUITests: XCTestCase {
    private let timeout: TimeInterval = 5

    private func fixture() -> XCUIApplication {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_FIXTURE"] = "enabled"
        app.launch()
        XCTAssertTrue(app.otherElements["fixture-root"].waitForExistence(timeout: timeout))
        return app
    }

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

    func testFixtureSelectsPackAndModeThenAnswersEverySeededType() {
        let app = fixture()

        app.buttons["Select pack"].tap()
        XCTAssertTrue(app.staticTexts["Select pack"].waitForExistence(timeout: timeout))
        app.buttons["Security+"].tap()
        XCTAssertTrue(app.staticTexts["Today"].waitForExistence(timeout: timeout))

        app.buttons["Select mode"].tap()
        XCTAssertTrue(app.staticTexts["Select mode"].waitForExistence(timeout: timeout))
        app.buttons["Normal review"].tap()
        app.buttons["Start review"].tap()

        let expectedTypes = [
            "Single choice", "Scenario single choice", "Select all", "True or false", "Matching"
        ]
        for (index, type) in expectedTypes.enumerated() {
            XCTAssertTrue(app.staticTexts[type].waitForExistence(timeout: timeout), "Missing seeded type \(type)")
            app.buttons["fixture-answer"].tap()
            XCTAssertTrue(app.staticTexts["Feedback"].waitForExistence(timeout: timeout))
            if index == 0 {
                app.buttons["Retry missed"].tap()
                XCTAssertTrue(app.staticTexts[type].waitForExistence(timeout: timeout))
                app.buttons["fixture-answer"].tap()
                XCTAssertTrue(app.staticTexts["Feedback"].waitForExistence(timeout: timeout))
                XCTAssertTrue(app.staticTexts["Attempts recorded: 2"].exists)
            }
            app.buttons[index == expectedTypes.count - 1 ? "Finish Session" : "Next question"].tap()
        }
        XCTAssertTrue(app.staticTexts["Session complete"].waitForExistence(timeout: timeout))
    }

    func testFixtureIssueReportPreviewsContextBeforeQueueing() {
        let app = fixture()
        app.buttons["Start review"].tap()
        app.buttons["Report"].tap()
        XCTAssertTrue(app.staticTexts["Report question"].waitForExistence(timeout: timeout))
        XCTAssertTrue(app.staticTexts["Preview"].waitForExistence(timeout: timeout))
        XCTAssertTrue(app.staticTexts["Reports include question context only."].exists)
        app.buttons["Queue report"].tap()
        XCTAssertTrue(app.staticTexts["Question"].waitForExistence(timeout: timeout))
    }

    func testFixturePendingConflictAndOfflineRecoveryAreVisibleAndRetryable() {
        let app = fixture()
        app.buttons["Start review"].tap()
        app.buttons["Pending sync"].tap()
        XCTAssertTrue(app.staticTexts["Pending sync"].waitForExistence(timeout: timeout))
        app.buttons["Recover offline"].tap()
        XCTAssertTrue(app.staticTexts["Offline recovery ready"].waitForExistence(timeout: timeout))
        app.buttons["Retry"].tap()
        XCTAssertTrue(app.staticTexts["Today"].waitForExistence(timeout: timeout))

        app.buttons["Start review"].tap()
        app.buttons["Conflict"].tap()
        XCTAssertTrue(app.staticTexts["Conflict detected"].waitForExistence(timeout: timeout))
        app.buttons["Retry"].tap()
        XCTAssertTrue(app.staticTexts["Today"].waitForExistence(timeout: timeout))
    }
}
