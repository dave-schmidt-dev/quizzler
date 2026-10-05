import XCTest
import Foundation

extension QuizWorkflowUITests {
    struct TodayCounters {
        let number: Int
        let count: Int
        let answered: Int
    }

    /// C14: the Learn row hides while the hero offers learning, so a test that
    /// wants the learn session taps whichever surface Today shows. The row is
    /// preferred when present, because there the hero starts a due review.
    @discardableResult
    func startLearnSession(_ app: XCUIApplication) -> XCUIElement {
        // The hero is always on Today, so wait on it rather than on a row that
        // may be hidden by design.
        let heroStart = app.buttons["today-hero-start"]
        XCTAssertTrue(heroStart.waitForExistence(timeout: timeout),
                      "Today shows neither the Learn row nor the learning hero")
        let learnNew = app.buttons["today-learn-new"]
        let start = learnNew.exists ? learnNew : heroStart
        start.tap()
        return start
    }

    /// The pack-order position ("Question N of M") sits on the Learn row while
    /// Today shows it, and on the hero while the hero replaces the row (C14).
    func todayPositionValue(_ app: XCUIApplication) -> String {
        let heroStart = app.buttons["today-hero-start"]
        XCTAssertTrue(heroStart.waitForExistence(timeout: timeout),
                      "Today never appeared; the catalog may have loaded no pack")
        let learnNew = app.buttons["today-learn-new"]
        return (learnNew.exists ? learnNew : heroStart).value as? String ?? ""
    }

    func todayCounters(_ app: XCUIApplication) throws -> TodayCounters {
        let positionValue = todayPositionValue(app)
        let place = try integers(in: positionValue, matching: #"^Question (\d+) of (\d+)$"#)
        let attempts = try progressAttemptCounters(app)
        return TodayCounters(number: place[0], count: place[1], answered: attempts.answered)
    }

    /// Cumulative attempts are durable course history, so this regression
    /// reads the Progress metric rather than a transient Today footer.
    func progressAttemptCounters(_ app: XCUIApplication) throws -> (correct: Int, answered: Int) {
        app.buttons["Progress"].tap()
        let coverage = app.descendants(matching: .any)["progress-coverage"]
        XCTAssertTrue(coverage.waitForExistence(timeout: timeout), "Progress coverage did not load")

        let attemptsValue = app.staticTexts["progress-attempts"]
        XCTAssertTrue(attemptsValue.waitForExistence(timeout: timeout), "Progress no longer exposes Attempts totals")
        let attempts = try integers(in: attemptsValue.label, matching: #"^(\d+) of (\d+)$"#)
        app.buttons["Today"].tap()
        XCTAssertTrue(app.buttons["today-hero-start"].waitForExistence(timeout: timeout))
        return (correct: attempts[0], answered: attempts[1])
    }

    /// Answers whichever control the resumed question offers, and returns its
    /// pack-scoped identifier.
    ///
    /// The app studies installed packs (INV-12), so this test cannot know which
    /// question type it will land on. Choice and true/false questions are
    /// answerable without reading the content; a matching question is not, and
    /// it fails loudly rather than skipping, because the gate counts a skipped
    /// UI test as an incomplete run.
    func answerOneQuestion(_ app: XCUIApplication) throws -> String {
        let positionValue = todayPositionValue(app)
        let packQuestionCount = try integers(in: positionValue, matching: #"^Question (\d+) of (\d+)$"#)[1]
        let identifier = try startReview(app)

        let choice = app.buttons["question-choice-0"]
        if choice.waitForExistence(timeout: timeout) {
            choice.tap()
        } else {
            XCTFail("the resumed question offers no blind answer path; extend answerOneQuestion for its type")
            return identifier
        }

        tapCheckAnswerIfPresent(app)
        let next = app.buttons["Next question"]
        XCTAssertTrue(next.waitForExistence(timeout: timeout), "Feedback never appeared")
        next.tap()
        let nextReport = app.buttons["question-report"]
        XCTAssertTrue(nextReport.waitForExistence(timeout: timeout), "Next question did not return to the question state")
        guard let nextIdentifier = nextReport.value as? String else {
            XCTFail("question-report has no value")
            return identifier
        }
        XCTAssertTrue(
            nextIdentifier.range(of: #"^Question ID [^:]+::[^:]+$"#, options: .regularExpression) != nil,
            "next question id is not pack-scoped: \(nextIdentifier)"
        )
        XCTAssertFalse(app.otherElements["question-shell-feedback"].exists, "Feedback remained visible after advancing")
        if packQuestionCount > 1 {
            XCTAssertNotEqual(nextIdentifier, identifier, "Next question re-served the answered question")
        }
        return identifier
    }

    /// Multiple select and matching are checked with the pinned bottom bar's
    /// primary button, where Next question sits in feedback, so it is hittable
    /// without scrolling. Single-answer types check on tap and have no button.
    func tapCheckAnswerIfPresent(_ app: XCUIApplication) {
        let check = app.buttons["Check Answer"]
        guard check.exists else { return }
        XCTAssertTrue(check.isEnabled, "an answer was selected but Check Answer stayed disabled")
        XCTAssertTrue(check.isHittable, "Check Answer is not hittable without scrolling")
        check.tap()
    }

    func startReview(_ app: XCUIApplication) throws -> String {
        startLearnSession(app)
        let report = app.buttons["question-report"]
        XCTAssertTrue(report.waitForExistence(timeout: timeout))
        guard let qidValue = report.value as? String else {
            XCTFail("question-report has no value")
            return ""
        }
        XCTAssertTrue(
            qidValue.range(of: #"^Question ID [^:]+::[^:]+$"#, options: .regularExpression) != nil,
            "question id is not pack-scoped: \(qidValue)"
        )
        return qidValue
    }

    func assertQuestionStartsBelowHeader(_ app: XCUIApplication,
                                                file: StaticString = #filePath,
                                                line: UInt = #line) {
        let position = app.staticTexts["session-position"]
        let topic = app.staticTexts["question-topic"]
        let prompt = app.staticTexts["question-prompt"]
        let scroll = app.scrollViews["question-shell"]
        XCTAssertTrue(position.waitForExistence(timeout: timeout), file: file, line: line)
        XCTAssertTrue(topic.waitForExistence(timeout: timeout), file: file, line: line)
        XCTAssertTrue(prompt.waitForExistence(timeout: timeout), file: file, line: line)
        XCTAssertTrue(scroll.exists, file: file, line: line)
        XCTAssertGreaterThan(topic.frame.minY, position.frame.maxY,
                             "topic begins under the pinned header", file: file, line: line)
        XCTAssertGreaterThan(prompt.frame.minY, topic.frame.maxY,
                             "prompt begins above or inside the topic", file: file, line: line)
        XCTAssertLessThan(prompt.frame.minY, scroll.frame.maxY,
                          "prompt start is outside the visible question area", file: file, line: line)
    }

    func integers(in text: String, matching pattern: String) throws -> [Int] {
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

    struct UnreadableLabel: Error, CustomStringConvertible {
        let text: String
        let pattern: String
        var description: String { "label \(text.debugDescription) does not match \(pattern)" }
    }
}
