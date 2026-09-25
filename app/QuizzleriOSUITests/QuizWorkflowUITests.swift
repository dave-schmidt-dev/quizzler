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

        let heroStart = app.buttons["today-hero-start"]
        XCTAssertTrue(heroStart.waitForExistence(timeout: timeout))
        XCTAssertFalse(app.staticTexts["Ready when you are"].exists, "Today must lead with the actionable hero, not a greeting")
        XCTAssertTrue(heroStart.isHittable, "Hero start button is not hittable")

        // Bound counters, not literals: pack-order position from today-learn-new (C3).
        let learnNew = app.buttons["today-learn-new"]
        XCTAssertTrue(learnNew.waitForExistence(timeout: timeout))
        let positionValue = learnNew.value as? String ?? ""
        XCTAssertTrue(
            positionValue.range(of: #"^Question \d+ of \d+$"#, options: .regularExpression) != nil,
            "unexpected pack-order position text: \(positionValue)"
        )
        let syncStatus = app.descendants(matching: .any)["global-progress-status"]
        XCTAssertTrue(syncStatus.exists, "Today has no global sync status")

        let courseControl = app.buttons["today-change-course"]
        XCTAssertTrue(courseControl.waitForExistence(timeout: timeout), "Today has no header course control")
        XCTAssertTrue(courseControl.isHittable, "Today header course control is not hittable")
        XCTAssertLessThanOrEqual(courseControl.frame.maxX, syncStatus.frame.minX, "Today header course control and sync badge overlap")
        XCTAssertLessThan(courseControl.frame.minY, syncStatus.frame.maxY, "Today header course control and sync badge do not share row vertically")
        XCTAssertGreaterThan(courseControl.frame.maxY, syncStatus.frame.minY, "Today header course control and sync badge do not share row vertically")

        heroStart.tap()

        // The identifier is pack-scoped: "<packID>::<questionID>" (INV-2, C1).
        let report = app.buttons["question-report"]
        XCTAssertTrue(report.waitForExistence(timeout: timeout))
        let qid = report.value as? String ?? ""
        XCTAssertTrue(
            qid.range(of: #"^Question ID [^:]+::[^:]+$"#, options: .regularExpression) != nil,
            "question id is not pack-scoped: \(qid)"
        )
        XCTAssertTrue(report.isHittable)
        XCTAssertTrue(app.descendants(matching: .any)["global-progress-status"].exists, "Question has no global sync status")

        report.tap()
        XCTAssertTrue(app.staticTexts["Report question"].waitForExistence(timeout: timeout))
        let reportSyncBadges = app.descendants(matching: .any)
            .matching(identifier: "global-progress-status")
        let reportContext = app.descendants(matching: .any)["report-header-context"]
        XCTAssertTrue(reportContext.exists, "Report sheet has no header context")
        var reportSyncBadgeIndex: Int?
        let reportContextFrame = reportContext.frame
        for index in 0..<reportSyncBadges.count {
            let candidate = reportSyncBadges.element(boundBy: index)
            let candidateFrame = candidate.frame
            let sharesRow = candidateFrame.minY < reportContextFrame.maxY &&
                candidateFrame.maxY > reportContextFrame.minY
            let followsContext = candidateFrame.minX >= reportContextFrame.maxX
            if sharesRow && followsContext {
                reportSyncBadgeIndex = index
                break
            }
        }
        XCTAssertNotNil(reportSyncBadgeIndex, "Report sheet has no global sync status aligned with its header context")
        if let reportSyncBadgeIndex {
            let reportSyncBadge = reportSyncBadges.element(boundBy: reportSyncBadgeIndex)
            XCTAssertLessThanOrEqual(reportContext.frame.maxX, reportSyncBadge.frame.minX, "Report header context and sync badge overlap")
        }
        app.buttons["Cancel"].tap()
    }

    func testTodayActionSurfacesPublishLimitsAndRetryAvailability() {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launch()

        let learnNew = app.buttons["today-learn-new"]
        XCTAssertTrue(learnNew.waitForExistence(timeout: timeout))
        XCTAssertGreaterThanOrEqual(learnNew.frame.height + 0.001, 44)
        XCTAssertTrue((learnNew.value as? String ?? "").hasPrefix("Question "))

        let retryMissed = app.buttons["today-retry-missed"]
        XCTAssertTrue(retryMissed.waitForExistence(timeout: timeout))
        XCTAssertGreaterThanOrEqual(retryMissed.frame.height + 0.001, 44)
        XCTAssertFalse((retryMissed.value as? String ?? "").isEmpty, "Retry missed must state whether questions are available")

        let nextLimit = app.buttons["today-session-length"]
        XCTAssertTrue(nextLimit.waitForExistence(timeout: timeout))
        XCTAssertGreaterThanOrEqual(nextLimit.frame.height + 0.001, 44)
        XCTAssertTrue((nextLimit.value as? String ?? "").hasPrefix("Up to") || (nextLimit.value as? String ?? "") == "Whole pack")
    }

    func testReviewExplanationAndQuestionHistoryNavigation() {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launch()

        let explanationButton = app.buttons["today-how-reviews-work"]
        XCTAssertTrue(explanationButton.waitForExistence(timeout: timeout))
        explanationButton.tap()
        XCTAssertTrue(app.navigationBars["How reviews work"].waitForExistence(timeout: timeout))
        let wikipediaLink = app.descendants(matching: .any)["scheduled-reviews-wikipedia-link"]
        XCTAssertTrue(wikipediaLink.exists)
        XCTAssertTrue(wikipediaLink.label.localizedCaseInsensitiveContains("Spaced repetition on Wikipedia"))
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS[cd] %@", "correct answer moves up one level")).firstMatch.exists)
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS[cd] %@", "session length is a maximum")).firstMatch.exists)
        app.buttons["scheduled-reviews-done"].tap()

        app.buttons["today-learn-new"].tap()
        let sessionContext = app.staticTexts["session-context"]
        XCTAssertTrue(sessionContext.waitForExistence(timeout: timeout))
        XCTAssertEqual(sessionContext.label, "Course study")
        let pie = app.descendants(matching: .any)["question-leitner-pie"]
        XCTAssertTrue(pie.waitForExistence(timeout: timeout))
        XCTAssertEqual(pie.label, "Leitner level, not reviewed yet")
        let questionScroll = app.scrollViews["question-shell"]
        let historyButton = app.buttons["question-view-history"]
        let reportButton = app.buttons["question-report"]
        XCTAssertTrue(questionScroll.exists)
        XCTAssertTrue(historyButton.waitForExistence(timeout: timeout))
        XCTAssertTrue(reportButton.exists)
        for _ in 0..<3 {
            if historyButton.isHittable && historyButton.frame.maxY <= reportButton.frame.minY {
                break
            }
            questionScroll.swipeUp()
        }
        XCTAssertTrue(historyButton.isHittable, "Scroll the question content until View history is tappable")
        XCTAssertGreaterThanOrEqual(historyButton.frame.minY, questionScroll.frame.minY)
        XCTAssertLessThanOrEqual(historyButton.frame.maxY, reportButton.frame.minY, "View history remains behind the pinned Report/Skip bar")

        historyButton.tap()
        XCTAssertTrue(app.navigationBars["Question history"].waitForExistence(timeout: timeout))
        XCTAssertTrue(app.descendants(matching: .any)["question-history-summary"].waitForExistence(timeout: timeout))
        XCTAssertTrue(app.descendants(matching: .any)["question-history-empty"].exists)
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "Earlier review history is unavailable")).firstMatch.exists)
        app.buttons["Done"].tap()
    }

    /// The session exit belongs to the global safe-area row, not the question
    /// scroll view. Its placement is asserted independently of whether the
    /// installed pack happens to provide a tall prompt.
    func testQuestionExitRemainsInTheTopRowAfterScrolling() {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launch()

        let start = app.buttons["today-hero-start"]
        XCTAssertTrue(start.waitForExistence(timeout: timeout))
        start.tap()
        XCTAssertTrue(app.buttons["question-report"].waitForExistence(timeout: timeout))

        app.scrollViews.firstMatch.swipeUp()

        let exit = app.buttons["session-end"]
        let syncStatus = app.descendants(matching: .any)["global-progress-status"]
        XCTAssertTrue(exit.waitForExistence(timeout: timeout), "Question has no persistent Back to Today control")
        XCTAssertTrue(syncStatus.exists, "Question has no global sync status")
        XCTAssertTrue(exit.isHittable, "Back to Today is not hittable after scrolling question content")
        XCTAssertGreaterThanOrEqual(exit.frame.width, 44, "Back to Today is narrower than the minimum touch target")
        XCTAssertGreaterThanOrEqual(exit.frame.height, 44, "Back to Today is shorter than the minimum touch target")
        XCTAssertLessThanOrEqual(exit.frame.maxX, syncStatus.frame.minX, "Back to Today and sync badge overlap")
        XCTAssertLessThan(exit.frame.minY, syncStatus.frame.maxY, "Back to Today and sync badge do not share the top row")
        XCTAssertGreaterThan(exit.frame.maxY, syncStatus.frame.minY, "Back to Today and sync badge do not share the top row")

        exit.tap()
        XCTAssertTrue(app.buttons["today-hero-start"].waitForExistence(timeout: timeout), "Back to Today did not return to Today")
    }

    /// Exercises the installed pack and the actual Catalyst/iPhone layout.
    /// Entering from a scrolled Today screen and skipping a scrolled question
    /// must both reveal the next topic and prompt below the pinned header.
    func testQuestionStartsBelowPinnedHeaderAfterEntryAndSkip() throws {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launch()

        let learnNew = app.buttons["today-learn-new"]
        XCTAssertTrue(learnNew.waitForExistence(timeout: timeout))
        app.scrollViews.firstMatch.swipeUp()
        XCTAssertTrue(learnNew.isHittable, "Learn new is unreachable after scrolling Today")
        learnNew.tap()

        let position = app.staticTexts["session-position"]
        XCTAssertTrue(position.waitForExistence(timeout: timeout))
        let count = try integers(in: position.label, matching: #"^Question (\d+) of (\d+) in this session$"#)[1]
        XCTAssertGreaterThan(count, 1, "a one-question session cannot check a skip transition")
        assertQuestionStartsBelowHeader(app)

        let questionScroll = app.scrollViews["question-shell"]
        questionScroll.swipeUp()
        XCTAssertTrue(position.isHittable, "scrolling question content covered the position header")
        XCTAssertTrue(app.buttons["session-end"].isHittable, "scrolling question content covered Back to Today")

        let skip = app.buttons["question-skip"]
        XCTAssertTrue(skip.isHittable)
        skip.tap()
        expectation(for: NSPredicate(format: "label BEGINSWITH %@", "Question 2 of "), evaluatedWith: position)
        waitForExpectations(timeout: timeout)
        assertQuestionStartsBelowHeader(app)
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

        let endSession = app.buttons["session-end"]
        XCTAssertTrue(endSession.waitForExistence(timeout: timeout))
        endSession.tap()

        // Terminating mid-write would prove nothing, so wait for the
        // completed local checkpoint before killing the process.
        let savedStatus = app.descendants(matching: .any)
            .matching(NSPredicate(format: "identifier == %@ AND label == %@", "global-progress-status", "local progress saved"))
            .firstMatch
        XCTAssertTrue(savedStatus.waitForExistence(timeout: timeout * 2), "progress was not safely checkpointed before relaunch")
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

    /// Exercises the CloudKit-backed "last sync succeeded" status via a DEBUG-only,
    /// local-backed fake (`CloudStatusFixtureProgressRepository`). The fake
    /// reports `syncMode == .cloudKit`, so `LaunchpadProgressModel` runs its
    /// real cloud-sync state machine; `synchronize()` is scripted to succeed,
    /// which is the only way `.synced` / "last sync succeeded" is reachable
    /// (LaunchpadProgressModel.swift's `startSynchronization()`). No real CloudKit
    /// account or network is ever involved.
    func testCloudSyncSucceedingReportsProgressSynced() throws {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_CLOUD_STATUS"] = "synced"
        app.launch()

        _ = try answerOneQuestion(app)

        let endSession = app.buttons["session-end"]
        XCTAssertTrue(endSession.waitForExistence(timeout: timeout))
        endSession.tap()

        let syncButton = app.buttons["global-progress-status"]
        XCTAssertTrue(
            syncButton.waitForExistence(timeout: timeout * 2),
            "a scripted successful synchronize() never reported 'Synced'"
        )
        XCTAssertEqual(syncButton.label, "Synced")
        XCTAssertTrue(syncButton.isHittable, "Synced badge must be a tappable control")
        syncButton.tap()
        XCTAssertTrue(syncButton.waitForExistence(timeout: timeout))
    }

    /// Exercises the CloudKit-backed "progress saved here · sync pending"
    /// status via the same fake, scripted to throw a non-account-isolation
    /// error from `synchronize()` — the only path to `.syncPending`
    /// (LaunchpadProgressModel.swift's `startSynchronization()` catch branch).
    func testCloudSyncFailingReportsSyncPending() throws {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_CLOUD_STATUS"] = "sync-pending"
        app.launch()

        _ = try answerOneQuestion(app)

        let endSession = app.buttons["session-end"]
        XCTAssertTrue(endSession.waitForExistence(timeout: timeout))
        endSession.tap()

        XCTAssertTrue(
            app.buttons["global-progress-status"].waitForExistence(timeout: timeout * 2),
            "a scripted failing synchronize() never reported 'progress saved here · sync pending'"
        )
        let retry = app.buttons["global-progress-status"]
        XCTAssertEqual(retry.label, "progress saved here · sync pending")
        XCTAssertTrue(retry.isHittable, "Pending sync has no reachable retry control")
        retry.tap()
        XCTAssertTrue(app.buttons["global-progress-status"].waitForExistence(timeout: timeout))
    }

    /// The defect this covers: `finishQuestion` used to advance
    /// `(index + 1) % questionCount` forever, so `LaunchpadState.results` was
    /// assigned nowhere and a review had no end. Answering a full session must
    /// now land on the summary rather than serving an eleventh question.
    func testAFullSessionEndsOnTheSummaryInsteadOfWrappingForever() throws {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launch()

        let startSession = app.buttons["today-learn-new"]
        XCTAssertTrue(startSession.waitForExistence(timeout: timeout))
        startSession.tap()

        // Read the length from the running app rather than hardcoding it: the
        // session length is chosen on Today, and a test that assumes ten
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
            tapCheckAnswerIfPresent(app)
            let next = app.buttons["Next question"]
            XCTAssertTrue(next.waitForExistence(timeout: timeout), "Feedback never appeared on question \(answered + 1)")
            next.tap()
        }

        let heading = app.staticTexts["session-complete-heading"]
        XCTAssertTrue(heading.waitForExistence(timeout: timeout), "answering a full session never reached the summary")
        XCTAssertTrue(app.descendants(matching: .any)["global-progress-status"].exists, "Results has no global sync status")
        XCTAssertTrue(
            heading.label.range(of: #"^\d+ of \d+ right$"#, options: .regularExpression) != nil,
            "unexpected session heading text: \(heading.label)"
        )

        // Assert the buttons by their accessibility labels, which is what the
        // summary actually publishes — the visible titles are overridden.
        XCTAssertTrue(app.buttons["Return to Today"].exists, "the summary offers no way back to Today")
        let nextSessionOrRetry = app.buttons["Continue to next session"].exists ||
            app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Retry the ")).firstMatch.exists
        XCTAssertTrue(nextSessionOrRetry, "the summary offers neither next session nor retry")
    }

    /// Two walkthrough findings in one pass: a session never said which of the
    /// ten questions you were on, and a checked answer never named the right
    /// option. Both are read here from the running app, not from a fixture.
    func testASessionShowsItsPositionAndNamesTheRightAnswerAfterChecking() throws {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launch()

        let startSession = app.buttons["today-learn-new"]
        XCTAssertTrue(startSession.waitForExistence(timeout: timeout))
        startSession.tap()

        let position = app.staticTexts["session-position"]
        XCTAssertTrue(position.waitForExistence(timeout: timeout), "a session shows no position indicator")
        let first = try integers(in: position.label, matching: #"^Question (\d+) of (\d+) in this session$"#)
        XCTAssertEqual(first[0], 1, "the counter is not one-based")
        XCTAssertGreaterThan(first[1], 1, "a session of one question cannot show progress")
        let actualN = first[1]
        let expectedVisibleText = "Question 1 of \(actualN)"
        let visibleText = (position.value as? String).flatMap { $0.isEmpty ? nil : $0 } ?? position.label
        XCTAssertTrue(visibleText.contains(expectedVisibleText), "visible text does not display actual N: \(visibleText)")

        // Prove position is in the persistent top area and remains hittable after scrolling question content.
        let exit = app.buttons["session-end"]
        XCTAssertTrue(exit.waitForExistence(timeout: timeout), "Question has no persistent Back to Today control")
        let syncStatus = app.descendants(matching: .any)["global-progress-status"]
        XCTAssertTrue(syncStatus.exists, "Question has no global sync status")
        XCTAssertTrue(position.isHittable, "Session position indicator is not hittable before scroll")
        XCTAssertGreaterThanOrEqual(position.frame.minY, exit.frame.minY, "Session position is above the top header")

        app.scrollViews.firstMatch.swipeUp()
        XCTAssertTrue(position.isHittable, "Session position indicator must remain hittable after scrolling question content")
        XCTAssertGreaterThanOrEqual(position.frame.minY, exit.frame.minY, "Session position shifted outside top header area after scroll")
        app.scrollViews.firstMatch.swipeDown()

        // Nothing may be marked before the answer is checked, or the screen
        // gives the answer away to anyone who reads the rows.
        let choice = app.buttons["question-choice-0"]
        XCTAssertTrue(choice.waitForExistence(timeout: timeout))
        XCTAssertFalse((choice.value as? String ?? "").contains("correct"), "the right answer is marked before checking")
        choice.tap()
        tapCheckAnswerIfPresent(app)

        XCTAssertTrue(app.buttons["Next question"].waitForExistence(timeout: timeout))

        // Position indicator must remain hittable and stable on feedback, including after scrolling.
        XCTAssertTrue(position.isHittable, "Session position indicator must remain hittable on feedback")
        let feedbackPosition = try integers(in: position.label, matching: #"^Question (\d+) of (\d+) in this session$"#)
        XCTAssertEqual(feedbackPosition[0], 1, "the question count must remain stable on feedback")
        XCTAssertEqual(feedbackPosition[1], actualN, "session length changed on feedback")
        let feedbackVisibleText = (position.value as? String).flatMap { $0.isEmpty ? nil : $0 } ?? position.label
        XCTAssertTrue(feedbackVisibleText.contains(expectedVisibleText), "feedback visible text does not display actual N: \(feedbackVisibleText)")

        app.scrollViews.firstMatch.swipeUp()
        XCTAssertTrue(position.isHittable, "Session position indicator must remain hittable after scrolling on feedback")

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
        XCTAssertEqual(second[0], 2, "the count did not advance to 2")
        XCTAssertEqual(second[1], first[1], "the session length changed mid-session")
        assertQuestionStartsBelowHeader(app)
    }

    private struct TodayCounters {
        let number: Int
        let count: Int
        let answered: Int
    }

    private func todayCounters(_ app: XCUIApplication) throws -> TodayCounters {
        let learnNew = app.buttons["today-learn-new"]
        XCTAssertTrue(learnNew.waitForExistence(timeout: timeout), "Today never appeared; the catalog may have loaded no pack")
        guard let positionValue = learnNew.value as? String else {
            XCTFail("today-learn-new has no accessibility value")
            throw UnreadableLabel(text: "", pattern: #"^Question (\d+) of (\d+)$"#)
        }
        let place = try integers(in: positionValue, matching: #"^Question (\d+) of (\d+)$"#)
        let attempts = try progressAttemptCounters(app)
        return TodayCounters(number: place[0], count: place[1], answered: attempts.answered)
    }

    /// Cumulative attempts are durable course history, so this regression
    /// reads the Progress metric rather than a transient Today footer.
    private func progressAttemptCounters(_ app: XCUIApplication) throws -> (correct: Int, answered: Int) {
        app.buttons["Progress"].tap()
        let coverage = app.descendants(matching: .any)["progress-coverage"]
        XCTAssertTrue(coverage.waitForExistence(timeout: timeout), "Progress coverage did not load")

        let attemptsValue = app.staticTexts["progress-attempts"]
        XCTAssertTrue(attemptsValue.waitForExistence(timeout: timeout), "Progress no longer exposes Attempts totals")
        let attempts = try integers(in: attemptsValue.label, matching: #"^(\d+) of (\d+)$"#)
        app.buttons["Today"].tap()
        XCTAssertTrue(app.buttons["today-learn-new"].waitForExistence(timeout: timeout))
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
    private func answerOneQuestion(_ app: XCUIApplication) throws -> String {
        let learnNew = app.buttons["today-learn-new"]
        XCTAssertTrue(learnNew.waitForExistence(timeout: timeout))
        guard let positionValue = learnNew.value as? String else {
            XCTFail("today-learn-new has no accessibility value")
            return ""
        }
        let packQuestionCount = try integers(in: positionValue, matching: #"^Question (\d+) of (\d+)$"#)[1]
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

    /// Multiple select and matching are checked with a button at the end of the
    /// question's scroll content. On a long question it starts under the
    /// Report/Skip bar, where a tap lands on the bar, so scroll it clear first,
    /// as a learner would. Single-answer types check on tap and have no button.
    private func tapCheckAnswerIfPresent(_ app: XCUIApplication) {
        let check = app.buttons["Check Answer"]
        guard check.exists else { return }
        XCTAssertTrue(check.isEnabled, "an answer was selected but Check Answer stayed disabled")
        let scroll = app.scrollViews.firstMatch
        scroll.swipeUp()
        for _ in 0..<2 where !check.isHittable {
            scroll.swipeUp()
        }
        check.tap()
    }

    private func startReview(_ app: XCUIApplication) throws -> String {
        let learnNew = app.buttons["today-learn-new"]
        XCTAssertTrue(learnNew.waitForExistence(timeout: timeout))
        learnNew.tap()
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

    private func assertQuestionStartsBelowHeader(_ app: XCUIApplication,
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
