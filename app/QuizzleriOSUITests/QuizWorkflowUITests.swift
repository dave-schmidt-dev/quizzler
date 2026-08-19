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

    /// Walks the real Launchpad, not the fixture.
    ///
    /// Every assertion here is structural. The app bundles whatever packs are
    /// installed on the building machine (INV-12), so a literal course name or
    /// question ID would either be machine-specific or would be re-asserting
    /// the hardcoded content this screen was built to stop showing — the
    /// previous version of this test asserted exactly the three-question
    /// fixture that walkthrough finding 1 was about.
    func testTodayStartsReviewAndKeepsQuestionIdentityAndReportReachable() {
        let app = XCUIApplication()
        app.launch()

        let eyebrow = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "TODAY · ")).firstMatch
        XCTAssertTrue(eyebrow.waitForExistence(timeout: timeout), "Today eyebrow is missing; the catalog may have loaded no pack")
        XCTAssertGreaterThan(eyebrow.label.count, "TODAY · ".count, "the course name is empty")

        // Bound counters, not literals: a position of the form "Question N of M".
        let position = app.staticTexts["today-position"]
        XCTAssertTrue(position.waitForExistence(timeout: timeout))
        XCTAssertTrue(
            position.label.range(of: #"^Question \d+ of \d+$"#, options: .regularExpression) != nil,
            "unexpected position text: \(position.label)"
        )
        XCTAssertTrue(app.staticTexts["today-score"].exists)

        let startReview = app.buttons["Start review"]
        XCTAssertTrue(startReview.waitForExistence(timeout: timeout))
        startReview.tap()

        // The identifier is pack-scoped: "<packID>::<questionID>" (INV-2).
        let qid = app.staticTexts["question-qid"]
        XCTAssertTrue(qid.waitForExistence(timeout: timeout))
        XCTAssertTrue(
            qid.label.range(of: #"^Question ID [^:]+::[^:]+$"#, options: .regularExpression) != nil,
            "question id is not pack-scoped: \(qid.label)"
        )
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
