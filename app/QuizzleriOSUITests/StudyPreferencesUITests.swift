import XCTest
import Foundation

/// Drives the study preferences and the pending-sync retry through the real
/// Launchpad. Every launch uses the offline fixtures plus the synthetic pack,
/// so served counts and Today's offer are deterministic rather than whatever
/// this build happens to bundle, and every launch resets the device-local
/// study preferences so no test inherits or leaks a Settings default.
@MainActor
final class StudyPreferencesUITests: XCTestCase {
    let timeout: TimeInterval = 5

    private func launchApp(cloudStatus: String? = nil) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launchEnvironment["QUIZZLER_UI_TEST_RESET_PREFERENCES"] = "enabled"
        app.launchEnvironment["QUIZZLER_UI_TEST_SYNTHETIC_PACK"] = "45"
        if let cloudStatus {
            app.launchEnvironment["QUIZZLER_UI_TEST_CLOUD_STATUS"] = cloudStatus
        }
        app.launch()
        return app
    }

    override func tearDown() {
        // A test's preferences must not leak into any later launch on this
        // simulator: relaunch once with the reset environment, then terminate.
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launchEnvironment["QUIZZLER_UI_TEST_RESET_PREFERENCES"] = "enabled"
        app.launch()
        app.terminate()
        super.tearDown()
    }

    func testSettingsDefaultSessionLengthChangesServedBatch() throws {
        let app = launchApp()
        waitForToday(app)

        openSettings(app)
        let picker = app.buttons["settings-default-session-limit"]
        choose("20 questions", from: picker, in: app)
        assertLabelContains("20 questions", on: picker,
                            message: "the Settings default did not change to 20 questions")
        backToToday(app)

        startSession(app)
        XCTAssertEqual(try servedBatchSize(app), 20,
                       "a session did not serve the Settings default of 20 questions")
        endSession(app)
    }

    func testTodayOneTimeOverrideIsConsumedOnlyByAValidStart() throws {
        let app = launchApp()
        waitForToday(app)

        let menu = app.descendants(matching: .any)["today-session-length"]
        assertValue("Up to 10 questions", on: menu,
                    message: "Today did not start from the Settings default")
        choose("20 questions", from: menu, in: app)
        assertValue("Up to 20 questions", on: menu,
                    message: "Today did not adopt the one-time choice")

        startSession(app)
        XCTAssertEqual(try servedBatchSize(app), 20,
                       "the one-time choice did not size the session it started")
        endSession(app)

        assertValue("Up to 10 questions", on: menu,
                    message: "the one-time choice outlived the session it sized")
        openSettings(app)
        assertLabelContains("10 questions",
                            on: app.buttons["settings-default-session-limit"],
                            message: "the one-time choice rewrote the Settings default")
    }

    func testScheduledReviewToggleChangesTodaysOffer() throws {
        let app = launchApp()
        let before = todayOfferTitle(app)

        openSettings(app)
        let toggle = app.switches["settings-scheduled-review"]
        // The row's centre is its label; only the inner switch flips it.
        let toggleSwitch = toggle.switches.firstMatch
        assertValue("1", on: toggle, message: "scheduled reviews do not default to on")
        toggleSwitch.tap()
        assertValue("0", on: toggle, message: "tapping the switch did not turn scheduled reviews off")
        backToToday(app)

        // With nothing due, hiding scheduled reviews cannot change the offer;
        // with due items, it must hide the review. Assert on the hero text,
        // never on a due count.
        let off = todayOfferTitle(app)
        if before.hasPrefix("Scheduled review") {
            XCTAssertFalse(off.hasPrefix("Scheduled review"),
                           "turning scheduled reviews off left the review offer on Today: \(off)")
        } else {
            XCTAssertEqual(off, before,
                           "turning scheduled reviews off changed an offer that was not a review: \(off)")
        }

        openSettings(app)
        toggleSwitch.tap()
        assertValue("1", on: toggle, message: "tapping the switch did not turn scheduled reviews back on")
        backToToday(app)
        XCTAssertEqual(todayOfferTitle(app), before,
                       "turning scheduled reviews back on did not restore Today's offer")
    }

    func testPendingSyncSucceedsOnRetry() throws {
        let app = launchApp(cloudStatus: "sync-pending-then-synced")
        waitForToday(app)
        XCTAssertTrue(statusBadge(app, label: "Synced").waitForExistence(timeout: timeout),
                      "the launch baseline sync never reported Synced")

        startSession(app)
        let choice = app.buttons["question-choice-0"]
        XCTAssertTrue(choice.waitForExistence(timeout: timeout),
                      "the synthetic question offers no blind answer path")
        choice.tap()

        // The scripted failure leaves the answer saved locally with the
        // transfer pending; the badge is the retry control for exactly that.
        let pending = statusBadge(app, label: "progress saved here · sync pending")
        XCTAssertTrue(pending.waitForExistence(timeout: timeout),
                      "answering did not surface the pending-sync state")
        pending.tap()
        XCTAssertTrue(pending.waitForNonExistence(timeout: timeout),
                      "tapping the pending badge did not retry the scripted sync")

        endSession(app)
        XCTAssertTrue(statusBadge(app, label: "Synced").waitForExistence(timeout: timeout),
                      "the retried sync never reported Synced")
    }

    // MARK: - Launchpad helpers

    private func waitForToday(_ app: XCUIApplication) {
        XCTAssertTrue(app.buttons["today-hero-start"].waitForExistence(timeout: timeout),
                      "Today never became ready; progress or the synthetic pack never loaded")
    }

    private func openSettings(_ app: XCUIApplication) {
        app.buttons["Settings"].tap()
        XCTAssertTrue(app.descendants(matching: .any)["settings-default-session-limit"]
            .waitForExistence(timeout: timeout), "Settings never appeared")
    }

    private func backToToday(_ app: XCUIApplication) {
        app.buttons["Today"].tap()
        waitForToday(app)
    }

    /// The hero always starts a session; the Learn row appears only while the
    /// hero offers due reviews, so tap whichever surface Today shows.
    private func startSession(_ app: XCUIApplication) {
        let hero = app.buttons["today-hero-start"]
        XCTAssertTrue(hero.waitForExistence(timeout: timeout), "Today shows no session start")
        let learnNew = app.buttons["today-learn-new"]
        (learnNew.exists ? learnNew : hero).tap()
        XCTAssertTrue(app.buttons["question-report"].waitForExistence(timeout: timeout),
                      "starting a session never opened a question")
    }

    private func endSession(_ app: XCUIApplication) {
        let end = app.buttons["session-end"]
        XCTAssertTrue(end.waitForExistence(timeout: timeout),
                      "the session header exposes no end control")
        end.tap()
        waitForToday(app)
    }

    /// The pinned header captions a session "Question N of M in this session";
    /// M is the served batch size.
    private func servedBatchSize(_ app: XCUIApplication) throws -> Int {
        let position = app.staticTexts["session-position"]
        XCTAssertTrue(position.waitForExistence(timeout: timeout),
                      "the session header shows no position")
        return try integers(in: position.label,
                            matching: #"^Question (\d+) of (\d+) in this session$"#)[1]
    }

    /// The hero's recommendation title. Learn and caught-up titles are fixed;
    /// a scheduled review names its batch, so that case matches by prefix.
    private func todayOfferTitle(_ app: XCUIApplication) -> String {
        waitForToday(app)
        for known in ["Ready to learn", "Ready to practice"] where app.staticTexts[known].exists {
            return known
        }
        let review = app.staticTexts
            .matching(NSPredicate(format: "label BEGINSWITH %@", "Scheduled review"))
            .firstMatch
        XCTAssertTrue(review.waitForExistence(timeout: timeout),
                      "Today shows none of the known recommendation titles")
        return review.label
    }

    /// Opens a picker or menu and taps the option with exactly this label.
    private func choose(_ option: String, from control: XCUIElement, in app: XCUIApplication) {
        XCTAssertTrue(control.waitForExistence(timeout: timeout), "the choice control never appeared")
        control.tap()
        let choice = app.buttons[option]
        XCTAssertTrue(choice.waitForExistence(timeout: timeout), "no \(option) option was offered")
        choice.tap()
    }

    /// The shared badge, matched by its accessibility label so a state change
    /// asserts on what the learner is told, not on the control kind.
    private func statusBadge(_ app: XCUIApplication, label: String) -> XCUIElement {
        app.descendants(matching: .any)
            .matching(identifier: "global-progress-status")
            .matching(NSPredicate(format: "label == %@", label))
            .firstMatch
    }

    private func assertValue(_ expected: String, on element: XCUIElement, message: String) {
        let expectation = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "value == %@", expected),
            object: element
        )
        XCTAssertEqual(XCTWaiter().wait(for: [expectation], timeout: timeout), .completed, message)
    }

    /// A Settings menu picker reports its selection in its label
    /// ("Session length, 10 questions"), not in its value.
    private func assertLabelContains(_ fragment: String, on element: XCUIElement, message: String) {
        let expectation = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "label CONTAINS %@", fragment),
            object: element
        )
        XCTAssertEqual(XCTWaiter().wait(for: [expectation], timeout: timeout), .completed, message)
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
