import XCTest
import Foundation

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

    /// The claim in `docs/WALKTHROUGH-2026-08-18.md` that the position survives
    /// a relaunch, observed rather than asserted.
    ///
    /// `StudyPositionTests` covers the arithmetic and the source shape; neither
    /// exercises the link the claim actually rests on, which is that the answer
    /// reaches the local store and the next launch reads it back. The app writes
    /// to Application Support inside its own container, and `terminate()` kills
    /// only the process, so the second launch sees what the first one saved.
    func testAnsweringAQuestionMovesTheCourseForwardAcrossARelaunch() throws {
        let app = XCUIApplication()
        app.launch()

        let before = try todayCounters(app)
        let answered = try answerOneQuestion(app)

        // Terminating mid-write would prove nothing, so wait for the save the
        // header reports before killing the process.
        XCTAssertTrue(
            app.staticTexts["local progress saved"].waitForExistence(timeout: timeout * 4),
            "progress was never saved locally, so a relaunch cannot prove durability"
        )
        app.terminate()
        app.launch()

        let after = try todayCounters(app)
        XCTAssertEqual(after.count, before.count, "the installed pack changed between launches")
        XCTAssertEqual(after.answered, before.answered + 1, "the recorded answer did not survive the relaunch")
        // Wraps at the end of the pack, which is what `answered % count` means.
        XCTAssertEqual(
            after.number,
            before.number % before.count + 1,
            "the position did not advance across the relaunch"
        )

        // The counters could advance while the screen still served the same
        // question, so check the question itself.
        if before.count > 1 {
            XCTAssertNotEqual(try startReview(app), answered, "the relaunched session re-served the answered question")
        }
    }

    private struct TodayCounters {
        let number: Int
        let count: Int
        let answered: Int
    }

    private func todayCounters(_ app: XCUIApplication) throws -> TodayCounters {
        let position = app.staticTexts["today-position"]
        XCTAssertTrue(position.waitForExistence(timeout: timeout), "Today never appeared; the catalog may have loaded no pack")
        let place = try integers(in: position.label, matching: #"^Question (\d+) of (\d+)$"#)
        let score = app.staticTexts["today-score"]
        XCTAssertTrue(score.waitForExistence(timeout: timeout))
        let tally = try integers(in: score.label, matching: #"^(\d+) correct of (\d+) answered$"#)
        return TodayCounters(number: place[0], count: place[1], answered: tally[1])
    }

    /// Answers whichever control the resumed question offers, and returns its
    /// pack-scoped identifier.
    ///
    /// The app studies installed packs (INV-12), so this test cannot know which
    /// question type it will land on. Choice and true/false questions are
    /// answerable without reading the content; a matching question is not, and
    /// it fails loudly rather than skipping, because the gate counts a skipped
    /// UI test as an incomplete run.
    private func answerOneQuestion(_ app: XCUIApplication) throws -> String {
        let identifier = try startReview(app)

        let choice = app.buttons["question-choice-0"]
        if choice.waitForExistence(timeout: timeout) {
            choice.tap()
        } else if app.buttons["question-true"].exists {
            app.buttons["question-true"].tap()
        } else {
            XCTFail("the resumed question offers no blind answer path; extend answerOneQuestion for its type")
            return identifier
        }

        let check = app.buttons["Check Answer"]
        XCTAssertTrue(check.isEnabled, "an answer was selected but Check Answer stayed disabled")
        check.tap()
        let finish = app.buttons["Finish Session"]
        XCTAssertTrue(finish.waitForExistence(timeout: timeout), "Feedback never appeared")
        finish.tap()
        XCTAssertTrue(app.staticTexts["Session complete"].waitForExistence(timeout: timeout))
        return identifier
    }

    private func startReview(_ app: XCUIApplication) throws -> String {
        let startReview = app.buttons["Start review"]
        XCTAssertTrue(startReview.waitForExistence(timeout: timeout))
        startReview.tap()
        let qid = app.staticTexts["question-qid"]
        XCTAssertTrue(qid.waitForExistence(timeout: timeout))
        return qid.label
    }

    private func integers(in text: String, matching pattern: String) throws -> [Int] {
        let regex = try NSRegularExpression(pattern: pattern)
        let whole = NSRange(text.startIndex..<text.endIndex, in: text)
        guard let match = regex.firstMatch(in: text, range: whole), match.numberOfRanges > 1 else {
            throw UnreadableLabel(text: text, pattern: pattern)
        }
        return try (1..<match.numberOfRanges).map { group in
            guard let range = Range(match.range(at: group), in: text), let value = Int(text[range]) else {
                throw UnreadableLabel(text: text, pattern: pattern)
            }
            return value
        }
    }

    private struct UnreadableLabel: Error, CustomStringConvertible {
        let text: String
        let pattern: String
        var description: String { "label \(text.debugDescription) does not match \(pattern)" }
    }
}
