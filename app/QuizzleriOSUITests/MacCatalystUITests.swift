#if targetEnvironment(macCatalyst)
import XCTest
import Foundation

/// C4 + Task 17: the Mac Catalyst UI track. This class exists only in a
/// Catalyst build of the UI-test bundle, so the iOS Simulator build has no
/// such class, and it skips even on a Catalyst destination unless the
/// milestone runner opted in. Four layers keep it out of the simulator gates:
///
/// 1. the whole class is `#if targetEnvironment(macCatalyst)`;
/// 2. `setUpWithError` skips unless `QUIZZLER_MAC_MILESTONE=1`;
/// 3. `test-gate.sh` and the hooks never name it, and `-only-testing` filters
///    it out of the simulator run;
/// 4. `SessionCommandsTests` fails if the gate script ever starts naming it.
///
/// The attended entry point is `scripts/mac_milestone_ui_tests.sh`, which
/// runs this class on a `platform=macOS,variant=Mac Catalyst` destination.
@MainActor
final class MacCatalystUITests: XCTestCase {
    private let timeout: TimeInterval = 8

    override func setUpWithError() throws {
        try super.setUpWithError()
        // The second lock: even on a Catalyst destination the class skips
        // unless the milestone runner set the opt-in environment.
        try XCTSkipUnless(
            ProcessInfo.processInfo.environment["QUIZZLER_MAC_MILESTONE"] == "1",
            "Mac Catalyst UI tests run only at a milestone, via scripts/mac_milestone_ui_tests.sh"
        )
    }

    /// The synthetic pack makes the session deterministic: every question is
    /// single-choice whose first option is correct, so keyboard digits can
    /// answer blind and the session length is the durable default.
    private func launchApp() -> XCUIApplication {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launchEnvironment["QUIZZLER_UI_TEST_RESET_PREFERENCES"] = "enabled"
        app.launchEnvironment["QUIZZLER_UI_TEST_SYNTHETIC_PACK"] = "45"
        app.launch()
        XCTAssertTrue(
            app.buttons["today-hero-start"].waitForExistence(timeout: timeout),
            "Today never became ready; progress or the synthetic pack never loaded"
        )
        return app
    }

    // MARK: - Tab bar (Task 17)

    func testTabBarSitsAtTheBottomOfTheWindowAndSwitchesScreens() {
        let app = launchApp()

        let tabBar = app.descendants(matching: .any)["mac-tab-bar"]
        XCTAssertTrue(tabBar.waitForExistence(timeout: timeout), "the Mac tab bar never appeared on Today")
        XCTAssertGreaterThan(tabBar.frame.minY, app.frame.midY, "the tab bar is not at the bottom of the window")

        // Catalyst hosts TabView's own tabs in the window toolbar, where a
        // phone-width window collapses them into a titlebar popup. The app
        // hides that system bar, so no pop-up button naming a tab destination
        // may remain. The exact query is this track's first attended probe.
        let tabPopup = app.popUpButtons.matching(
            NSPredicate(
                format: "label CONTAINS[cd] %@ OR label CONTAINS[cd] %@ OR label CONTAINS[cd] %@",
                "Today", "Progress", "Settings"
            )
        ).firstMatch
        XCTAssertFalse(tabPopup.exists, "the window toolbar still offers a tab popup button")

        // Each tab switch changes the visible screen.
        app.buttons["mac-tab-progress"].tap()
        XCTAssertTrue(
            app.descendants(matching: .any)["progress-coverage"].waitForExistence(timeout: timeout),
            "the Progress tab switch changed no screen"
        )
        app.buttons["mac-tab-settings"].tap()
        XCTAssertTrue(
            app.descendants(matching: .any)["settings-app-version"].waitForExistence(timeout: timeout),
            "the Settings tab switch changed no screen"
        )
        app.buttons["mac-tab-today"].tap()
        XCTAssertTrue(
            app.buttons["today-hero-start"].waitForExistence(timeout: timeout),
            "the Today tab switch changed no screen"
        )
    }

    func testTabBarLeavesDuringASessionAndReturnsAfterEscape() {
        let app = launchApp()
        let tabBar = app.descendants(matching: .any)["mac-tab-bar"]
        XCTAssertTrue(tabBar.waitForExistence(timeout: timeout), "the Mac tab bar never appeared on Today")

        app.buttons["today-hero-start"].tap()
        XCTAssertTrue(
            app.buttons["question-report"].waitForExistence(timeout: timeout),
            "the hero started no session"
        )
        XCTAssertFalse(tabBar.exists, "the tab bar stayed visible during a session")

        // Escape is the keyboard's way out (C4).
        app.typeKey(.escape, modifierFlags: [])
        XCTAssertTrue(
            app.buttons["today-hero-start"].waitForExistence(timeout: timeout),
            "Escape did not end the session and return to Today"
        )
        XCTAssertTrue(
            tabBar.waitForExistence(timeout: timeout),
            "the tab bar did not come back after the session"
        )
    }

    // MARK: - Keyboard session (C4)

    /// The C4 "done when": a whole session runs from the keyboard — digit,
    /// Return, Return, …, S, Escape. Key 1 answers (the synthetic pack's
    /// first option is always correct), Return advances, S skips the second
    /// question, and Escape is covered by the tab-bar session test above.
    func testAWholeSessionRunsFromTheKeyboard() throws {
        let app = launchApp()
        app.buttons["today-hero-start"].tap()

        let position = app.staticTexts["session-position"]
        XCTAssertTrue(position.waitForExistence(timeout: timeout), "the session shows no position indicator")
        let sessionLength = try sessionLength(from: position)
        XCTAssertGreaterThanOrEqual(
            sessionLength, 3,
            "a session shorter than three questions cannot exercise answer, skip, and finish from the keyboard"
        )

        for answered in 0..<sessionLength {
            // The second question is skipped with S; skip records nothing
            // and the position advances (C4).
            if answered == 1 {
                app.typeKey("s", modifierFlags: [])
                expectation(
                    for: NSPredicate(format: "label BEGINSWITH %@", "Question 3 of "),
                    evaluatedWith: position
                )
                waitForExpectations(timeout: timeout)
                continue
            }

            // Key 1 picks the first option; single-choice commits on it.
            app.typeKey("1", modifierFlags: [])
            let primary = app.buttons[answered == sessionLength - 1 ? "Finish session" : "Next question"]
            XCTAssertTrue(
                primary.waitForExistence(timeout: timeout),
                "key 1 never reached feedback on question \(answered + 1)"
            )
            // Return advances; on the last question it finishes the session.
            app.typeKey(.return, modifierFlags: [])
            if answered < sessionLength - 1 {
                XCTAssertTrue(
                    app.buttons["question-report"].waitForExistence(timeout: timeout),
                    "Return did not advance to question \(answered + 2)"
                )
            }
        }

        XCTAssertTrue(
            app.staticTexts["session-complete-heading"].waitForExistence(timeout: timeout),
            "the keyboard-driven session never reached the summary"
        )
    }

    // MARK: - Helpers

    /// The "Question N of M in this session" counter's M.
    private func sessionLength(from position: XCUIElement) throws -> Int {
        let label = position.label
        let regex = try NSRegularExpression(pattern: #"^Question (\d+) of (\d+) in this session$"#)
        let whole = NSRange(label.startIndex..<label.endIndex, in: label)
        guard let match = regex.firstMatch(in: label, range: whole),
              match.numberOfRanges > 2,
              let range = Range(match.range(at: 2), in: label),
              let count = Int(label[range]) else {
            throw UnreadableSessionPosition(label: label)
        }
        return count
    }

    private struct UnreadableSessionPosition: Error, CustomStringConvertible {
        let label: String
        var description: String {
            "session position label \(label.debugDescription) is unreadable"
        }
    }
}
#endif
