import Foundation
import XCTest

/// C17: explanatory notes moved out of primary content into on-demand info
/// popovers (and Settings section footers) stay reachable and readable.
@MainActor
final class InfoPopoverUITests: XCTestCase {
    private let timeout: TimeInterval = 8

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

    /// The synthetic pack replaces the bundled catalog, so a test that needs
    /// a real course (the CySA+ lab) launches without it.
    private func launchApp(syntheticPack: Bool = true) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launchEnvironment["QUIZZLER_UI_TEST_RESET_PREFERENCES"] = "enabled"
        if syntheticPack {
            app.launchEnvironment["QUIZZLER_UI_TEST_SYNTHETIC_PACK"] = "45"
        }
        app.launch()
        XCTAssertTrue(app.buttons["today-hero-start"].waitForExistence(timeout: timeout),
                      "Today never became ready; progress or the synthetic pack never loaded")
        return app
    }

    func testProgressNotesMoveIntoInfoPopovers() {
        let app = launchApp()
        app.buttons["Progress"].tap()
        XCTAssertTrue(app.descendants(matching: .any)["progress-coverage"].waitForExistence(timeout: timeout),
                      "Progress never appeared")

        XCTAssertFalse(app.staticTexts["Distinct pack questions encountered"].exists,
                       "The coverage note is still inline on Progress")
        openInfo(app.buttons["info-coverage"], message: "info-coverage-message", in: app)

        XCTAssertFalse(app.staticTexts["Total answers submitted (not distinct questions)"].exists,
                       "The attempts note is still inline on Progress")
        openInfo(app.buttons["info-attempts"], message: "info-attempts-message", in: app)

        XCTAssertFalse(app.staticTexts["History is bounded to roughly the last 200 answers."].exists,
                       "The history-bound note is still inline on Progress")
        openInfo(app.buttons["info-history-bound"], message: "info-history-bound-message", in: app)
    }

    func testScheduledReviewPausedNoteMovesIntoInfoPopover() {
        let app = launchApp()
        app.buttons["Settings"].tap()

        let toggle = app.switches["settings-scheduled-review"]
        XCTAssertTrue(toggle.waitForExistence(timeout: timeout), "Settings never appeared")
        toggle.switches.firstMatch.tap()

        app.buttons["Progress"].tap()
        let info = app.buttons["info-scheduled-paused"]
        XCTAssertTrue(info.waitForExistence(timeout: timeout), "The paused review card exposes no info control")
        XCTAssertFalse(
            app.staticTexts["Previously seen questions stay saved. Turn it on in Settings to resume scheduled review."].exists,
            "The paused review note is still inline on Progress"
        )
        openInfo(info, message: "info-scheduled-paused-message", in: app)
    }

    func testTodaySessionLengthNoteAndHowReviewsWork() {
        let app = launchApp()

        let howReviewsWork = app.buttons["today-how-reviews-work"]
        reveal(howReviewsWork, in: app)
        XCTAssertTrue(howReviewsWork.isHittable, "How reviews work is not tappable without scrolling")
        howReviewsWork.tap()
        XCTAssertTrue(app.navigationBars["How reviews work"].waitForExistence(timeout: timeout),
                      "How reviews work did not open the explanation")
        XCTAssertTrue(app.buttons["scheduled-reviews-done"].waitForExistence(timeout: timeout))
        app.buttons["scheduled-reviews-done"].tap()

        XCTAssertFalse(app.staticTexts["Next session only"].exists,
                       "The session-length note is still inline on Today")
        openInfo(app.buttons["info-session-length"], message: "info-session-length-message", in: app)
    }

    func testSettingsCaptionsBecomeSectionFooters() {
        let app = launchApp()
        app.buttons["Settings"].tap()

        let defaultLimit = app.descendants(matching: .any)["settings-default-session-limit"]
        XCTAssertTrue(defaultLimit.waitForExistence(timeout: timeout), "Settings never appeared")
        XCTAssertTrue(app.switches["settings-scheduled-review"].exists,
                      "The scheduled review switch is missing from Settings")
        XCTAssertTrue(app.descendants(matching: .any)["settings-maximum-leitner-level"].exists,
                      "The maximum Leitner level picker is missing from Settings")

        let howReviewsWork = app.buttons["settings-how-reviews-work"]
        reveal(howReviewsWork, in: app)
        XCTAssertTrue(howReviewsWork.isHittable, "Settings' How reviews work is not tappable")
        howReviewsWork.tap()
        XCTAssertTrue(app.navigationBars["How reviews work"].waitForExistence(timeout: timeout),
                      "Settings' How reviews work did not open the explanation")
        XCTAssertTrue(app.buttons["scheduled-reviews-done"].waitForExistence(timeout: timeout))
        app.buttons["scheduled-reviews-done"].tap()

        let studyFooter = staticText(containing: "Maximum questions for each new session.", in: app)
        reveal(studyFooter, in: app)
        XCTAssertTrue(staticText(containing: "Scheduled review of previously seen questions.", in: app).exists,
                      "The scheduled review note is not in the Study footer")
        XCTAssertTrue(staticText(containing: "Correct answers stop at this level.", in: app).exists,
                      "The maximum level note is not in the Study footer")

        let aboutFooter = staticText(containing: "Question packs and your selected course stay on this device.", in: app)
        reveal(aboutFooter, in: app)
        XCTAssertTrue(aboutFooter.exists, "The About statement is not in the About section footer")
    }

    /// The lab lives behind the CySA+ course, which CurriculumLabUITests
    /// already requires the build to install.
    func testLabPinnedLockerNoteMovesIntoInfoPopover() throws {
        let app = launchApp(syntheticPack: false)
        let changeCourse = app.buttons["today-change-course"]
        XCTAssertTrue(changeCourse.waitForExistence(timeout: timeout), "Today header course control is missing")
        changeCourse.tap()
        let courseCard = app.buttons
            .matching(NSPredicate(format: "identifier BEGINSWITH %@", "course-card-cysa-plus/"))
            .firstMatch
        XCTAssertTrue(courseCard.waitForExistence(timeout: timeout), "The CySA+ course card is missing")
        courseCard.tap()

        let openLab = app.buttons["today-learning-lab"]
        reveal(openLab, in: app)
        openLab.tap()

        let labScroll = app.scrollViews["lab-content-scroll"]
        XCTAssertTrue(labScroll.waitForExistence(timeout: timeout), "The learning lab never opened")
        let lessonContinue = app.buttons["lab-lesson-continue"]
        revealLab(lessonContinue, in: app)
        lessonContinue.tap()

        let answers = [
            ("check-auth-record", "observation"),
            ("check-phishing", "inference"),
            ("check-process", "observation"),
            ("check-scope", "inference")
        ]
        for (checkID, answer) in answers {
            let choice = app.buttons["lab-check-\(checkID)-\(answer)"]
            revealLab(choice, in: app)
            choice.tap()
        }
        let checkContinue = app.buttons["lab-concept-continue"]
        revealLab(checkContinue, in: app)
        checkContinue.tap()

        XCTAssertTrue(app.buttons["lab-submit-handoff"].waitForExistence(timeout: timeout),
                      "The investigation phase never appeared")
        XCTAssertFalse(
            app.staticTexts["No items pinned. Pin at least two distinct sources (e.g. Authentication + Endpoint)."].exists,
            "The pinned locker instruction is still inline in the lab"
        )
        let info = app.buttons["info-lab-pins"]
        revealLab(info, in: app)
        info.tap()
        XCTAssertTrue(app.descendants(matching: .any)["info-lab-pins-message"].waitForExistence(timeout: timeout),
                      "The lab pinned locker popover did not present")
    }

    // MARK: - Helpers

    private func staticText(containing fragment: String, in app: XCUIApplication) -> XCUIElement {
        app.staticTexts
            .containing(NSPredicate(format: "label CONTAINS %@", fragment))
            .firstMatch
    }

    private func openInfo(_ button: XCUIElement, message: String, in app: XCUIApplication) {
        reveal(button, in: app)
        button.tap()
        XCTAssertTrue(app.descendants(matching: .any)[message].waitForExistence(timeout: timeout),
                      "The info popover did not present: \(button.identifier)")
        dismissPopover(app, message: message)
    }

    private func dismissPopover(_ app: XCUIApplication, message: String) {
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.03, dy: 0.5)).tap()
        XCTAssertTrue(app.descendants(matching: .any)[message].waitForNonExistence(timeout: timeout),
                      "The info popover did not dismiss: \(message)")
    }

    private func reveal(_ element: XCUIElement, in app: XCUIApplication) {
        XCTAssertTrue(element.waitForExistence(timeout: timeout), "Missing control: \(element.identifier)")
        for _ in 0..<6 where !element.isHittable {
            app.swipeUp()
        }
        XCTAssertTrue(element.isHittable, "Control is not reachable by scrolling: \(element.identifier)")
    }

    /// Mirrors CurriculumLabUITests: the investigation pins its checklist bar
    /// over the scroll's bottom edge, so a control under it reports hittable
    /// but is covered.
    private func revealLab(_ element: XCUIElement, in app: XCUIApplication) {
        XCTAssertTrue(element.waitForExistence(timeout: timeout), "Missing control: \(element.identifier)")
        let scroll = app.scrollViews["lab-content-scroll"]
        XCTAssertTrue(scroll.waitForExistence(timeout: timeout), "The lab content scroll is missing")
        for _ in 0..<20 {
            let bar = app.otherElements["lab-investigation-bar"]
            let visibleBottom = bar.exists ? min(bar.frame.minY, scroll.frame.maxY) : scroll.frame.maxY
            let midY = element.frame.midY
            if element.isHittable && midY > scroll.frame.minY && midY < visibleBottom { return }
            let from = scroll.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.6))
            let to = scroll.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: midY <= scroll.frame.minY ? 0.85 : 0.35))
            from.press(forDuration: 0.05, thenDragTo: to)
        }
        XCTAssertTrue(element.isHittable, "Control is not reachable by scrolling: \(element.identifier)")
    }
}
