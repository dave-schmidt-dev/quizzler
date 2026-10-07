import XCTest
import Foundation

/// C20: the on-demand review tour. One ordered walk through every primary
/// screen, attaching one screenshot per screen for a human reviewer; it
/// documents rather than asserts, and never joins a gate (the captures run
/// from `scripts/review_captures.sh`).
///
/// The synthetic pack keeps the study half machine-independent; the CySA+ lab
/// needs the real catalog, so the second launch drops the synthetic pack and
/// records a note instead of failing when that course is not installed.
@MainActor
final class ReviewCaptureUITests: XCTestCase {
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

    func testCaptureEveryScreenForReview() {
        let synthetic = launchApp(syntheticPack: true)
        captureStudyScreens(synthetic)
        synthetic.terminate()
        captureLabScreens()
    }

    // MARK: - Study tour (synthetic pack)

    private func captureStudyScreens(_ app: XCUIApplication) {
        waitForScreen(app.buttons["today-hero-start"], "Today never became ready")
        captureReviewScreen(app, "01-today", in: self)

        app.buttons["today-change-course"].tap()
        waitForLabel("Your courses", on: app.staticTexts["courses-heading"], "Your courses never appeared")
        captureReviewScreen(app, "02-courses", in: self)
        app.buttons["courses-back-to-today"].tap()
        waitForScreen(app.buttons["today-hero-start"], "Your courses did not return to Today")

        app.buttons["Progress"].tap()
        waitForScreen(app.descendants(matching: .any)["progress-coverage"], "Progress never appeared")
        captureReviewScreen(app, "03-progress", in: self)
        captureInfo(app, "info-coverage", "04-progress-coverage-info")
        captureInfo(app, "info-attempts", "05-progress-attempts-info")
        captureInfo(app, "info-history-bound", "06-progress-history-info")

        app.buttons["Settings"].tap()
        waitForLabel("Offer scheduled reviews", on: app.switches["settings-scheduled-review"], "Settings never appeared")
        captureReviewScreen(app, "07-settings", in: self)

        // The paused scheduled-review note only exists while the switch is off.
        app.switches["settings-scheduled-review"].switches.firstMatch.tap()
        app.buttons["Progress"].tap()
        let paused = app.buttons["info-scheduled-paused"]
        for _ in 0..<6 where !paused.isHittable {
            app.swipeDown()
        }
        waitForScreen(paused, "The paused scheduled-review card never appeared")
        captureReviewScreen(app, "08-progress-scheduled-paused", in: self)
        captureInfo(app, "info-scheduled-paused", "09-progress-scheduled-paused-info")

        app.buttons["Today"].tap()
        waitForScreen(app.buttons["today-hero-start"], "Today never reappeared")
        captureSession(app)
        waitForScreen(app.buttons["today-hero-start"], "The summary did not return to Today")
        captureInfo(app, "info-session-length", "14-today-session-length-info")
    }

    /// Answering the synthetic pack's ten single-choice questions walks
    /// question → feedback → summary without reading any content.
    private func captureSession(_ app: XCUIApplication) {
        let hero = app.buttons["today-hero-start"]
        for _ in 0..<6 where !hero.isHittable {
            app.swipeDown()
        }
        XCTAssertTrue(hero.isHittable, "Today's hero is not reachable")
        hero.tap()

        let report = app.buttons["question-report"]
        waitForScreen(report, "Starting a session never opened a question")
        captureReviewScreen(app, "10-question", in: self)

        report.tap()
        waitForScreen(app.staticTexts["Report question"], "The report sheet never appeared")
        captureReviewScreen(app, "11-report-sheet", in: self)
        app.buttons["Cancel"].tap()
        XCTAssertTrue(app.staticTexts["Report question"].waitForNonExistence(timeout: timeout),
                      "The report sheet did not close")

        answerCurrentQuestion(app)
        captureReviewScreen(app, "12-question-feedback", in: self)

        for _ in 0..<9 {
            let next = app.buttons["Next question"]
            waitForScreen(next, "Feedback never offered the next question")
            next.tap()
            answerCurrentQuestion(app)
        }

        let finish = app.buttons["Finish session"]
        waitForScreen(finish, "The last question never offered to finish the session")
        finish.tap()
        waitForLabel("right", on: app.staticTexts["session-complete-heading"], "The session summary never appeared")
        captureReviewScreen(app, "13-summary", in: self)
        app.buttons["Return to Today"].tap()
    }

    private func answerCurrentQuestion(_ app: XCUIApplication) {
        let choice = app.buttons["question-choice-0"]
        waitForScreen(choice, "A question offered no blind answer path")
        choice.tap()
        waitForScreen(app.scrollViews["question-shell-feedback"], "Feedback never appeared")
    }

    // MARK: - Lab tour (real catalog)

    private func captureLabScreens() {
        let app = launchApp(syntheticPack: false)
        guard app.buttons["today-hero-start"].waitForExistence(timeout: timeout) else {
            recordSkippedLabCaptures()
            return
        }

        app.buttons["today-change-course"].tap()
        waitForScreen(app.staticTexts["courses-heading"], "Your courses never appeared")
        let courseCard = app.buttons
            .matching(NSPredicate(format: "identifier BEGINSWITH %@", "course-card-cysa-plus/"))
            .firstMatch
        guard courseCard.waitForExistence(timeout: timeout) else {
            recordSkippedLabCaptures()
            return
        }
        courseCard.tap()
        waitForScreen(app.buttons["today-hero-start"], "Selecting CySA+ did not return to Today")

        let openLab = app.buttons["today-learning-lab"]
        reveal(openLab, in: app)
        captureReviewScreen(app, "15-today-learning-lab", in: self)
        openLab.tap()

        let labScroll = app.scrollViews["lab-content-scroll"]
        waitForScreen(labScroll, "The learning lab never opened")
        waitForScreen(app.staticTexts["Investigate signals with evidence"], "The lab lesson never appeared")
        captureReviewScreen(app, "16-lab-lesson", in: self)

        let lessonContinue = app.buttons["lab-lesson-continue"]
        reveal(lessonContinue, in: app, scroll: labScroll)
        lessonContinue.tap()

        let checkContinue = app.buttons["lab-concept-continue"]
        waitForScreen(checkContinue, "The concept check never appeared")
        scrollToTop(labScroll)
        captureReviewScreen(app, "17-lab-concept-check", in: self)

        let answers = [
            ("check-auth-record", "observation"),
            ("check-phishing", "inference"),
            ("check-process", "observation"),
            ("check-scope", "inference")
        ]
        for (checkID, answer) in answers {
            let choice = app.buttons["lab-check-\(checkID)-\(answer)"]
            reveal(choice, in: app, scroll: labScroll)
            choice.tap()
        }
        captureReviewScreen(app, "18-lab-concept-check-answered", in: self)

        reveal(checkContinue, in: app, scroll: labScroll)
        checkContinue.tap()
        waitForScreen(app.otherElements["lab-investigation-bar"], "The investigation never appeared")
        scrollToTop(labScroll)
        captureReviewScreen(app, "19-lab-investigation", in: self)
        captureInfo(app, "info-lab-pins", "20-lab-pins-info", scroll: labScroll)

        let authSource = app.buttons["lab-source-authentication"]
        reveal(authSource, in: app, scroll: labScroll)
        authSource.tap()
        let authPin = app.buttons["lab-pin-auth-1"]
        reveal(authPin, in: app, scroll: labScroll)
        authPin.tap()

        let endpointSource = app.buttons["lab-source-endpoint"]
        reveal(endpointSource, in: app, scroll: labScroll)
        endpointSource.tap()
        let endpointPin = app.buttons["lab-pin-ep-2"]
        reveal(endpointPin, in: app, scroll: labScroll)
        endpointPin.tap()

        let scopeQuery = app.buttons["lab-query-query-user"]
        reveal(scopeQuery, in: app, scroll: labScroll)
        scopeQuery.tap()

        let response = app.buttons["lab-response-isolate"]
        reveal(response, in: app, scroll: labScroll)
        response.tap()
        captureReviewScreen(app, "21-lab-handoff-prepared", in: self)

        let noteEditor = app.textViews["lab-handoff-note"]
        reveal(noteEditor, in: app, scroll: labScroll)
        noteEditor.tap()
        noteEditor.typeText("Preserve FIN-17 evidence before isolation for review.")

        let submit = app.buttons["lab-submit-handoff"]
        XCTAssertTrue(submit.isEnabled, "The handoff never became ready to submit")
        XCTAssertTrue(submit.isHittable, "The submit control is not hittable")
        submit.tap()
        waitForScreen(app.staticTexts["Investigation Debrief"], "The debrief never appeared")
        scrollToTop(labScroll)
        captureReviewScreen(app, "22-lab-debrief", in: self)
    }

    // MARK: - Helpers

    private func launchApp(syntheticPack: Bool) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launchEnvironment["QUIZZLER_UI_TEST_RESET_PREFERENCES"] = "enabled"
        if syntheticPack {
            app.launchEnvironment["QUIZZLER_UI_TEST_SYNTHETIC_PACK"] = "45"
        }
        app.launch()
        return app
    }

    private func recordSkippedLabCaptures() {
        let note = XCTAttachment(string: "Lab captures skipped: the CySA+ course is not installed in this build.")
        note.name = "lab-captures-skipped"
        note.lifetime = .keepAlways
        add(note)
    }

    private func captureInfo(_ app: XCUIApplication, _ identifier: String, _ name: String, scroll: XCUIElement? = nil) {
        let button = app.buttons[identifier]
        reveal(button, in: app, scroll: scroll)
        button.tap()
        let message = app.descendants(matching: .any)["\(identifier)-message"]
        waitForScreen(message, "The info popover did not present: \(identifier)")
        captureReviewScreen(app, name, in: self)
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.03, dy: 0.5)).tap()
        XCTAssertTrue(message.waitForNonExistence(timeout: timeout),
                      "The info popover did not dismiss: \(identifier)")
    }

    private func waitForScreen(_ element: XCUIElement, _ message: String) {
        XCTAssertTrue(element.waitForExistence(timeout: timeout), message)
    }

    private func scrollToTop(_ scrollView: XCUIElement) {
        for _ in 0..<4 {
            scrollView.swipeDown()
        }
    }

    /// assertLabelContains-style waiting for a screen whose label names it.
    private func waitForLabel(_ fragment: String, on element: XCUIElement, _ message: String) {
        let expectation = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "label CONTAINS %@", fragment),
            object: element
        )
        XCTAssertEqual(XCTWaiter().wait(for: [expectation], timeout: timeout), .completed, message)
    }

    private func reveal(_ element: XCUIElement, in app: XCUIApplication, scroll: XCUIElement? = nil) {
        waitForScreen(element, "Missing control: \(element.identifier)")
        guard let scroll else {
            for _ in 0..<6 where !element.isHittable {
                app.swipeUp()
            }
            XCTAssertTrue(element.isHittable, "Control is not reachable by scrolling: \(element.identifier)")
            return
        }

        waitForScreen(scroll, "The lab content scroll is missing")
        // The investigation pins its checklist bar over the scroll's bottom edge.
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
