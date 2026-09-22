import XCTest
import Foundation

@MainActor
final class QuizWorkflowUITests: XCTestCase {
    private let timeout: TimeInterval = 5

    private func fixture() -> XCUIApplication {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_FIXTURE"] = "enabled"
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
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
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
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
    /// The StudyResumePosition tests cover pack-local indexing; this test
    /// exercises the link the claim actually rests on, which is that the answer
    /// reaches local storage and the next launch reads it back. The app writes
    /// to Application Support inside its own container, and `terminate()` kills
    /// only the process, so the second launch sees what the first one saved.
    func testAnsweringAQuestionMovesTheCourseForwardAcrossARelaunch() throws {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launch()

        let before = try todayCounters(app)
        let answered = try answerOneQuestion(app)

        // Terminating mid-write would prove nothing, so wait for the
        // completed local checkpoint before killing the process.
        XCTAssertTrue(
            [
                "local progress saved"
            ].contains {
                app.staticTexts[$0].waitForExistence(timeout: timeout * 2)
            },
            "progress was neither synced nor safely checkpointed before relaunch"
        )
        app.terminate()
        let relaunchedApp = XCUIApplication()
        relaunchedApp.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        relaunchedApp.launch()

        let after = try todayCounters(relaunchedApp)
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
            XCTAssertNotEqual(
                try startReview(relaunchedApp),
                answered,
                "the relaunched session re-served the answered question"
            )
        }
    }

    /// Exercises the CloudKit-backed "progress synced" status via a DEBUG-only,
    /// local-backed fake (`CloudStatusFixtureProgressRepository`). The fake
    /// reports `syncMode == .cloudKit`, so `LaunchpadProgressModel` runs its
    /// real cloud-sync state machine; `synchronize()` is scripted to succeed,
    /// which is the only way `.synced` / "progress synced" is reachable
    /// (LaunchpadView.swift's `startSynchronization()`). No real CloudKit
    /// account or network is ever involved.
    func testCloudSyncSucceedingReportsProgressSynced() throws {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_CLOUD_STATUS"] = "synced"
        app.launch()

        _ = try answerOneQuestion(app)

        XCTAssertTrue(
            app.staticTexts["progress synced"].waitForExistence(timeout: timeout * 2),
            "a scripted successful synchronize() never reported 'progress synced'"
        )
    }

    /// Exercises the CloudKit-backed "progress saved here · sync pending"
    /// status via the same fake, scripted to throw a non-account-isolation
    /// error from `synchronize()` — the only path to `.syncPending`
    /// (LaunchpadView.swift's `startSynchronization()` catch branch).
    func testCloudSyncFailingReportsSyncPending() throws {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_CLOUD_STATUS"] = "sync-pending"
        app.launch()

        _ = try answerOneQuestion(app)

        XCTAssertTrue(
            app.staticTexts["progress saved here · sync pending"].waitForExistence(timeout: timeout * 2),
            "a scripted failing synchronize() never reported 'progress saved here · sync pending'"
        )
    }

    /// The defect this covers: `finishQuestion` used to advance
    /// `(index + 1) % questionCount` forever, so `LaunchpadState.results` was
    /// assigned nowhere and a review had no end. Answering a full session must
    /// now land on the summary rather than serving an eleventh question.
    func testAFullSessionEndsOnTheSummaryInsteadOfWrappingForever() throws {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launch()

        let startReview = app.buttons["Start review"]
        XCTAssertTrue(startReview.waitForExistence(timeout: timeout))
        startReview.tap()

        // Read the length from the running app rather than hardcoding it: the
        // session length is a Settings choice now, and a test that assumes ten
        // would fail for the setting rather than for the defect it covers.
        let position = app.staticTexts["session-position"]
        XCTAssertTrue(position.waitForExistence(timeout: timeout))
        let sessionLength = try integers(in: position.label, matching: #"^Question (\d+) of (\d+) in this session$"#)[1]

        // One more iteration than the session holds, so a session that failed to
        // end is caught by the loop rather than by the assertion after it.
        for answered in 0..<(sessionLength + 1) {
            // Wait only on the iteration where the summary is expected: the
            // transition out of the last question has to render first, and an
            // instantaneous check there would read as "still answering".
            if app.staticTexts["session-complete-heading"].waitForExistence(timeout: answered == sessionLength ? timeout : 0) {
                XCTAssertEqual(answered, sessionLength, "the session ended after \(answered) answers, not \(sessionLength)")
                break
            }
            let choice = app.buttons["question-choice-0"]
            if choice.waitForExistence(timeout: timeout) {
                choice.tap()
            } else if app.buttons["question-true"].exists {
                app.buttons["question-true"].tap()
            } else {
                XCTFail("question \(answered + 1) offers no blind answer path")
                return
            }
            app.buttons["Check Answer"].tap()
            let next = app.buttons["Next question"]
            XCTAssertTrue(next.waitForExistence(timeout: timeout), "Feedback never appeared on question \(answered + 1)")
            next.tap()
        }

        let heading = app.staticTexts["session-complete-heading"]
        XCTAssertTrue(heading.waitForExistence(timeout: timeout), "answering a full session never reached the summary")
        XCTAssertEqual(heading.label, "Session complete")

        // Assert the buttons by their accessibility labels, which is what the
        // summary actually publishes — the visible titles are overridden.
        XCTAssertTrue(app.buttons["Return to Today"].exists, "the summary offers no way back to Today")
        XCTAssertTrue(app.buttons["Continue to next session"].exists)
        XCTAssertTrue(
            app.staticTexts.matching(NSPredicate(format: "label ENDSWITH %@", " answered")).firstMatch.exists,
            "the summary shows no score for the session just finished"
        )
    }

    /// Two walkthrough findings in one pass: a session never said which of the
    /// ten questions you were on, and a checked answer never named the right
    /// option. Both are read here from the running app, not from a fixture.
    func testASessionShowsItsPositionAndNamesTheRightAnswerAfterChecking() throws {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launch()

        let startReview = app.buttons["Start review"]
        XCTAssertTrue(startReview.waitForExistence(timeout: timeout))
        startReview.tap()

        let position = app.staticTexts["session-position"]
        XCTAssertTrue(position.waitForExistence(timeout: timeout), "a session shows no position indicator")
        let first = try integers(in: position.label, matching: #"^Question (\d+) of (\d+) in this session$"#)
        XCTAssertEqual(first[0], 1, "the counter is not one-based")
        XCTAssertGreaterThan(first[1], 1, "a session of one question cannot show progress")

        // Nothing may be marked before the answer is checked, or the screen
        // gives the answer away to anyone who reads the rows.
        let choice = app.buttons["question-choice-0"]
        XCTAssertTrue(choice.waitForExistence(timeout: timeout))
        XCTAssertFalse((choice.value as? String ?? "").contains("correct"), "the right answer is marked before checking")
        choice.tap()
        app.buttons["Check Answer"].tap()

        XCTAssertTrue(app.buttons["Next question"].waitForExistence(timeout: timeout))
        // A multiple-select question has more than one right option, so the
        // assertion is "at least one named", not "exactly one".
        let values = (0..<8)
            .map { app.buttons["question-choice-\($0)"] }
            .filter(\.exists)
            .map { $0.value as? String ?? "" }
        XCTAssertFalse(values.isEmpty, "the checked question rendered no choice rows")
        XCTAssertGreaterThanOrEqual(
            values.filter { $0.contains("correct") }.count,
            1,
            "checking an answer named no option correct: \(values)"
        )
        // The row the learner picked is either the right one or is named as
        // theirs; it must never sit there unlabelled next to a marked row.
        let chosen = values[0]
        XCTAssertTrue(
            chosen.contains("correct") || chosen.contains("your answer"),
            "the chosen row reads \"\(chosen)\", which names it neither right nor the learner's"
        )

        app.buttons["Next question"].tap()
        let advanced = NSPredicate(format: "label BEGINSWITH %@", "Question 2 of ")
        expectation(for: advanced, evaluatedWith: position)
        waitForExpectations(timeout: timeout)
        let second = try integers(in: position.label, matching: #"^Question (\d+) of (\d+) in this session$"#)
        XCTAssertEqual(second[1], first[1], "the session length changed mid-session")
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
        let position = app.staticTexts["today-position"]
        XCTAssertTrue(position.waitForExistence(timeout: timeout))
        let packQuestionCount = try integers(in: position.label, matching: #"^Question (\d+) of (\d+)$"#)[1]
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
        let next = app.buttons["Next question"]
        XCTAssertTrue(next.waitForExistence(timeout: timeout), "Feedback never appeared")
        next.tap()
        let nextIdentifier = app.staticTexts["question-qid"]
        XCTAssertTrue(nextIdentifier.waitForExistence(timeout: timeout), "Next question did not return to the question state")
        XCTAssertFalse(app.otherElements["question-shell-feedback"].exists, "Feedback remained visible after advancing")
        if packQuestionCount > 1 {
            XCTAssertNotEqual(nextIdentifier.label, identifier, "Next question re-served the answered question")
        }
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
