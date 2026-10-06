import XCTest
import Foundation

// C3: a session left mid-plan is saved device-locally, and Today offers to
// continue it at the same question — both after leaving via Back to Today and
// after relaunching the app mid-session.
//
// Every assertion is structural: the installed pack decides the plan and the
// question ids, so the tests read them from the running app. XCUITest's
// `typeKey` does not deliver Return or Escape to SwiftUI shortcuts here, so
// both tests tap controls only.
extension QuizWorkflowUITests {
    func testLeavingAtQuestionThreeAndResumingKeepsThePlan() throws {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launch()

        let original = try walkToQuestionThree(app)

        let endSession = app.buttons["session-end"]
        XCTAssertTrue(endSession.waitForExistence(timeout: timeout), "the question has no Back to Today control")
        endSession.tap()

        let resume = app.buttons["today-resume-session"]
        XCTAssertTrue(resume.waitForExistence(timeout: timeout), "leaving mid-session offered no resume row")
        let resumeParts = try integers(in: resume.label, matching: #"^Resume session · (\d+) of (\d+)$"#)
        XCTAssertEqual(resumeParts[0], 3, "the resume row does not point at the third question: \(resume.label)")
        XCTAssertEqual(resumeParts[1], original.planCount, "the resume row lost part of the plan: \(resume.label)")

        resume.tap()

        let position = app.staticTexts["session-position"]
        XCTAssertTrue(position.waitForExistence(timeout: timeout), "resuming did not return to a question")
        XCTAssertEqual(position.label, "Question 3 of \(original.planCount) in this session")
        XCTAssertEqual(
            try currentQuestionIdentifier(app),
            original.questionID,
            "resuming served a different question than the one the session left"
        )
    }

    func testRelaunchMidSessionResumesAtTheSameQuestion() throws {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launch()

        let original = try walkToQuestionThree(app)
        app.terminate()

        // KEEP_SESSION stops the launch-time reset from deleting the file the
        // first launch saved, which is the mid-session relaunch this feature
        // exists for.
        let relaunchedApp = XCUIApplication()
        relaunchedApp.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        relaunchedApp.launchEnvironment["QUIZZLER_UI_TEST_KEEP_SESSION"] = "enabled"
        relaunchedApp.launch()

        let resume = relaunchedApp.buttons["today-resume-session"]
        XCTAssertTrue(resume.waitForExistence(timeout: timeout), "a mid-session relaunch offered no resume row")
        let resumeParts = try integers(in: resume.label, matching: #"^Resume session · (\d+) of (\d+)$"#)
        XCTAssertEqual(resumeParts[0], 3, "the relaunched resume row does not point at the third question: \(resume.label)")
        XCTAssertEqual(resumeParts[1], original.planCount, "the relaunched resume row lost part of the plan: \(resume.label)")

        resume.tap()

        let position = relaunchedApp.staticTexts["session-position"]
        XCTAssertTrue(position.waitForExistence(timeout: timeout), "resuming did not return to a question")
        XCTAssertEqual(position.label, "Question 3 of \(original.planCount) in this session")
        XCTAssertEqual(
            try currentQuestionIdentifier(relaunchedApp),
            original.questionID,
            "the relaunched resume served a different question than the one the session left"
        )
    }

    /// Starts a session and answers two questions, leaving it on question 3.
    /// Returns the plan count and the third question's pack-scoped id.
    private func walkToQuestionThree(_ app: XCUIApplication) throws -> (planCount: Int, questionID: String) {
        _ = try startReview(app)
        let position = app.staticTexts["session-position"]
        XCTAssertTrue(position.waitForExistence(timeout: timeout), "a session shows no position indicator")
        let planCount = try integers(in: position.label, matching: #"^Question (\d+) of (\d+) in this session$"#)[1]
        XCTAssertGreaterThanOrEqual(planCount, 3, "a session shorter than three questions cannot be left mid-plan")

        for _ in 0..<2 {
            let choice = app.buttons["question-choice-0"]
            XCTAssertTrue(choice.waitForExistence(timeout: timeout), "the question offers no blind answer path")
            choice.tap()
            tapCheckAnswerIfPresent(app)
            let next = app.buttons["Next question"]
            XCTAssertTrue(next.waitForExistence(timeout: timeout), "Feedback never appeared")
            next.tap()
            XCTAssertTrue(
                app.buttons["question-report"].waitForExistence(timeout: timeout),
                "Next question did not return to the question state"
            )
        }

        XCTAssertEqual(
            try integers(in: position.label, matching: #"^Question (\d+) of (\d+) in this session$"#)[0],
            3,
            "answering two questions did not land on the third"
        )
        return (planCount, try currentQuestionIdentifier(app))
    }

    /// The pack-scoped identifier of the question on screen.
    private func currentQuestionIdentifier(_ app: XCUIApplication) throws -> String {
        let report = app.buttons["question-report"]
        XCTAssertTrue(report.waitForExistence(timeout: timeout), "the question exposes no report control")
        guard let identifier = report.value as? String else {
            throw UnreadableLabel(text: "nil", pattern: "Question ID <pack>::<question>")
        }
        return identifier
    }
}
