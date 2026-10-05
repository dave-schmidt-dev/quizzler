import XCTest
import Foundation

@MainActor
final class QuizWorkflowUITests: XCTestCase {
    let timeout: TimeInterval = 5

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

        // Bound counters, not literals: pack-order position from Today's
        // learning entry point (C3), which the hero carries while the Learn
        // row is hidden (C14).
        let positionValue = todayPositionValue(app)
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

        // C14: the report sheet must not repeat the global sync badge; the
        // pinned header behind it already shows it. The count is compared
        // against the question screen's because that header stays in the
        // accessibility tree while the sheet is up.
        let syncBadgeCount = app.descendants(matching: .any)
            .matching(identifier: "global-progress-status")
            .count
        report.tap()
        XCTAssertTrue(app.staticTexts["Report question"].waitForExistence(timeout: timeout))
        let reportContext = app.descendants(matching: .any)["report-header-context"]
        XCTAssertTrue(reportContext.exists, "Report sheet has no header context")
        XCTAssertEqual(
            app.descendants(matching: .any).matching(identifier: "global-progress-status").count,
            syncBadgeCount,
            "Report sheet repeats the global sync status"
        )
        app.buttons["Cancel"].tap()
    }

    func testTodayActionSurfacesPublishLimitsAndRetryAvailability() {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launch()

        // C14: while the hero offers learning, the Learn row is hidden and the
        // hero carries the pack-order position instead. While the hero starts
        // due reviews, the row keeps that position.
        let heroStart = app.buttons["today-hero-start"]
        XCTAssertTrue(heroStart.waitForExistence(timeout: timeout))
        XCTAssertGreaterThanOrEqual(heroStart.frame.height + 0.001, 44)
        let learnNew = app.buttons["today-learn-new"]
        if learnNew.exists {
            XCTAssertEqual(heroStart.label, "Start review", "Today shows the Learn row while the hero does not offer reviews")
            XCTAssertGreaterThanOrEqual(learnNew.frame.height + 0.001, 44)
            XCTAssertTrue((learnNew.value as? String ?? "").hasPrefix("Question "))
        } else {
            XCTAssertTrue(
                heroStart.label == "Start learning" || heroStart.label == "Keep practicing",
                "the Learn row is hidden while the hero does not offer learning: \(heroStart.label)"
            )
            XCTAssertTrue(
                (heroStart.value as? String ?? "").hasPrefix("Question "),
                "the hero must carry the pack-order position while the Learn row is hidden"
            )
        }

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

        startLearnSession(app)

        // The Leitner card is part of feedback now, so answer before asserting it.
        let choice = app.buttons["question-choice-0"]
        XCTAssertTrue(choice.waitForExistence(timeout: timeout))
        // The mode label belongs to scheduled review and retry sessions only,
        // so a course-study session shows none.
        XCTAssertFalse(app.staticTexts["session-context"].exists, "Course study shows a session mode label")
        choice.tap()
        tapCheckAnswerIfPresent(app)
        XCTAssertTrue(app.buttons["Next question"].waitForExistence(timeout: timeout))

        let pie = app.descendants(matching: .any)["question-leitner-pie"]
        XCTAssertTrue(pie.waitForExistence(timeout: timeout))
        XCTAssertTrue(
            pie.label.range(of: #"^Leitner level \d+ of \d+$"#, options: .regularExpression) != nil,
            "answering did not move the pie off the unreviewed state: \(pie.label)"
        )
        let questionScroll = app.scrollViews["question-shell-feedback"]
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

        // Enter from a scrolled Today screen: through the Learn row when
        // Today shows it, otherwise through the hero after scrolling back to
        // it (C14).
        app.scrollViews.firstMatch.swipeUp()
        let learnNew = app.buttons["today-learn-new"]
        if learnNew.exists {
            XCTAssertTrue(learnNew.isHittable, "Learn new is unreachable after scrolling Today")
            learnNew.tap()
        } else {
            app.scrollViews.firstMatch.swipeDown()
            startLearnSession(app)
        }

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

    /// C4: number keys pick options and S skips, so a session runs without a
    /// pointer. Return and Escape are bound too, but XCUITest's `typeKey` does
    /// not deliver them to SwiftUI shortcuts in the iOS simulator (probed
    /// 2026-10-05), so they are covered by TASKS.md, not here.
    func testKeyboardShortcutsSkipAndAnswerAQuestion() throws {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launch()

        startLearnSession(app)

        let position = app.staticTexts["session-position"]
        XCTAssertTrue(position.waitForExistence(timeout: timeout))
        let count = try integers(in: position.label, matching: #"^Question (\d+) of (\d+) in this session$"#)[1]
        XCTAssertGreaterThan(count, 1, "a one-question session cannot observe a keyboard skip")

        app.typeKey("s", modifierFlags: [])
        expectation(for: NSPredicate(format: "label BEGINSWITH %@", "Question 2 of "), evaluatedWith: position)
        waitForExpectations(timeout: timeout)

        // Key 1 picks option 1; tap-to-answer types commit on it, the rest
        // need Check Answer. On a two-question session this is the last
        // question, whose primary button reads "Finish session" (C6).
        app.typeKey("1", modifierFlags: [])
        tapCheckAnswerIfPresent(app)
        let advanced = app.buttons["Next question"]
        let finished = app.buttons["Finish session"]
        XCTAssertTrue(
            advanced.waitForExistence(timeout: timeout) || finished.exists,
            "the number key never reached feedback"
        )
        XCTAssertTrue(
            app.buttons["question-choice-0"].value.map { "\($0)".contains("Selected") } ?? false,
            "key 1 did not select the first option"
        )
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
        // C13: pending sync is data safe on the device, so its style is a
        // warning; only a failed save or an account change is an error.
        XCTAssertEqual(
            retry.value as? String ?? "",
            "warning",
            "pending sync is styled as a failure rather than a warning"
        )
        XCTAssertTrue(retry.isHittable, "Pending sync has no reachable retry control")
        retry.tap()
        XCTAssertTrue(app.buttons["global-progress-status"].waitForExistence(timeout: timeout))
    }

    /// C13: manual refresh is a pull gesture on Today, not a hidden tap on
    /// the status badge. The scripted repository succeeds its startup baseline
    /// `synchronize()` and then follows the script, so a pull that runs
    /// synchronization is observable as the badge leaving "Synced" for the
    /// scripted pending state.
    func testTodayPullToRefreshRunsSynchronization() {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_CLOUD_STATUS"] = "sync-pending"
        app.launch()

        let syncedBadge = app.descendants(matching: .any)
            .matching(NSPredicate(format: "identifier == %@ AND label == %@", "global-progress-status", "Synced"))
            .firstMatch
        XCTAssertTrue(
            syncedBadge.waitForExistence(timeout: timeout * 2),
            "the scripted startup synchronize() never reported 'Synced'"
        )

        let pendingBadge = app.descendants(matching: .any)
            .matching(NSPredicate(format: "identifier == %@ AND label == %@", "global-progress-status", "progress saved here · sync pending"))
            .firstMatch

        // Drag from the top of the hero card, the first scroll content, down
        // through the scroll view to trip the refresh control.
        let hero = app.buttons["today-hero-start"]
        XCTAssertTrue(hero.waitForExistence(timeout: timeout))
        let pullStart = hero.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0))
        let pullEnd = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.85))
        for _ in 0..<2 {
            pullStart.press(forDuration: 0.3, thenDragTo: pullEnd)
            if pendingBadge.waitForExistence(timeout: timeout) { break }
        }
        XCTAssertTrue(
            pendingBadge.exists,
            "pull to refresh on Today did not run synchronization"
        )
    }

    /// The defect this covers: `finishQuestion` used to advance
    /// `(index + 1) % questionCount` forever, so `LaunchpadState.results` was
    /// assigned nowhere and a review had no end. Answering a full session must
    /// now land on the summary rather than serving an eleventh question.
    ///
    /// C6: the last question's primary button names where it goes, a missed
    /// row opens its correct answer and explanation, and Next session is one
    /// tap even when the session has misses.
    func testAFullSessionEndsOnTheSummaryInsteadOfWrappingForever() throws {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launch()

        startLearnSession(app)

        // Read the length from the running app rather than hardcoding it: the
        // session length is chosen on Today, and a test that assumes ten
        // would fail for the setting rather than for the defect it covers.
        let position = app.staticTexts["session-position"]
        XCTAssertTrue(position.waitForExistence(timeout: timeout))
        let sessionLength = try integers(in: position.label, matching: #"^Question (\d+) of (\d+) in this session$"#)[1]

        for answered in 0..<sessionLength {
            if app.staticTexts["session-complete-heading"].exists {
                XCTFail("the session ended after \(answered) answers, not \(sessionLength)")
                return
            }
            let choice = app.buttons["question-choice-0"]
            if choice.waitForExistence(timeout: timeout) {
                choice.tap()
            } else {
                XCTFail("question \(answered + 1) offers no blind answer path")
                return
            }
            tapCheckAnswerIfPresent(app)
            // The last question's primary button must say where it goes
            // rather than "Next question" (C6).
            let primary = app.buttons[answered == sessionLength - 1 ? "Finish session" : "Next question"]
            XCTAssertTrue(primary.waitForExistence(timeout: timeout), "Feedback never appeared on question \(answered + 1)")
            primary.tap()
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
        // Next session is reachable whether or not the blind answers missed
        // anything: primary when clean, secondary beside Retry otherwise (C6).
        XCTAssertTrue(app.buttons["Continue to next session"].exists, "the summary offers no next session action")

        // Blind answers cannot force a miss on single-choice questions, so
        // the missed-review flow is asserted whenever the session produced
        // a miss; a clean session is covered by the assertion above.
        let missedRow = app.buttons["session-missed-row-0"]
        if missedRow.waitForExistence(timeout: timeout) {
            let retry = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Retry the ")).firstMatch
            XCTAssertTrue(
                retry.exists,
                "a session with misses offers no retry action"
            )
            let rowPrompt = missedRow.label
            missedRow.tap()
            let prompt = app.staticTexts["missed-detail-prompt"]
            XCTAssertTrue(prompt.waitForExistence(timeout: timeout), "the missed row opened no detail view")
            XCTAssertEqual(prompt.label, rowPrompt, "the missed row opened a different question's detail")
            XCTAssertTrue(
                app.descendants(matching: .any)["missed-detail-correct-answer"].exists,
                "the missed detail names no correct answer"
            )
            XCTAssertTrue(
                app.descendants(matching: .any)["missed-detail-explanation"].waitForExistence(timeout: timeout),
                "the missed detail shows no explanation"
            )
            app.buttons["missed-detail-done"].tap()
            XCTAssertTrue(
                retry.waitForExistence(timeout: timeout),
                "closing the missed detail lost the summary"
            )
        }
    }

    /// Two walkthrough findings in one pass: a session never said which of the
    /// ten questions you were on, and a checked answer never named the right
    /// option. Both are read here from the running app, not from a fixture.
    ///
    /// The session runs against the scripted successful synchronize()
    /// (`QUIZZLER_UI_TEST_CLOUD_STATUS`), because a synced session is the case
    /// whose header shows no sync status.
    func testASessionShowsItsPositionAndNamesTheRightAnswerAfterChecking() throws {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_CLOUD_STATUS"] = "synced"
        app.launch()

        let syncedStatus = app.descendants(matching: .any)
            .matching(NSPredicate(format: "identifier == %@ AND label == %@", "global-progress-status", "Synced"))
            .firstMatch
        XCTAssertTrue(
            syncedStatus.waitForExistence(timeout: timeout * 2),
            "a scripted successful synchronize() never reported 'Synced'"
        )

        startLearnSession(app)

        let position = app.staticTexts["session-position"]
        XCTAssertTrue(position.waitForExistence(timeout: timeout), "a session shows no position indicator")
        let first = try integers(in: position.label, matching: #"^Question (\d+) of (\d+) in this session$"#)
        XCTAssertEqual(first[0], 1, "the counter is not one-based")
        XCTAssertGreaterThan(first[1], 1, "a session of one question cannot show progress")
        let actualN = first[1]
        let expectedVisibleText = "1/\(actualN)"
        let visibleText = (position.value as? String).flatMap { $0.isEmpty ? nil : $0 } ?? position.label
        XCTAssertTrue(visibleText.contains(expectedVisibleText), "visible text does not display actual N: \(visibleText)")

        // Prove position is in the persistent top area and remains hittable after scrolling question content.
        let exit = app.buttons["session-end"]
        XCTAssertTrue(exit.waitForExistence(timeout: timeout), "Question has no persistent Back to Today control")
        // When progress is synced the header shows no sync status.
        XCTAssertFalse(
            app.descendants(matching: .any)["global-progress-status"].exists,
            "a synced session header shows a sync status"
        )
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
        XCTAssertFalse(app.descendants(matching: .any)["question-leitner-card"].exists, "the Leitner card must not show while answering")
        choice.tap()
        tapCheckAnswerIfPresent(app)

        XCTAssertTrue(app.buttons["Next question"].waitForExistence(timeout: timeout))

        // The verdict sits directly under the prompt, so it reads before any
        // scrolling; the schedule card follows the explanation, not the prompt.
        let verdict = app.descendants(matching: .any)["question-verdict"]
        XCTAssertTrue(verdict.exists, "feedback shows no verdict under the prompt")
        XCTAssertTrue(verdict.isHittable, "the verdict is not visible without scrolling")
        XCTAssertTrue(verdict.label == "Correct" || verdict.label == "Incorrect", "unexpected verdict label: \(verdict.label)")
        XCTAssertTrue(app.descendants(matching: .any)["question-explanation"].exists, "feedback shows no explanation after the choices")
        XCTAssertTrue(app.descendants(matching: .any)["question-leitner-card"].exists, "feedback shows no Leitner card")

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
}
