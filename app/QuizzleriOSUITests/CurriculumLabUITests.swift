import Foundation
import XCTest

@MainActor
final class CurriculumLabUITests: XCTestCase {
    private let timeout: TimeInterval = 8

    func testInvestigationCompletionPresentsDebriefAndReplayClearsCaseDecisions() {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launch()

        // The lab row exists only while the CySA+ course is selected, so
        // select that course before the row can appear.
        selectCourse(in: app, matching: "course-card-cysa-plus/")

        let openLab = app.buttons["today-learning-lab"]
        XCTAssertTrue(openLab.waitForExistence(timeout: timeout))
        reveal(openLab, in: app.scrollViews.firstMatch)
        openLab.tap()

        let labScroll = app.scrollViews["lab-content-scroll"]
        XCTAssertTrue(labScroll.waitForExistence(timeout: timeout))
        XCTAssertTrue(app.staticTexts["Investigate signals with evidence"].waitForExistence(timeout: timeout))

        let lessonContinue = app.buttons["lab-lesson-continue"]
        reveal(lessonContinue, in: labScroll)
        lessonContinue.tap()

        let checkContinue = app.buttons["lab-concept-continue"]
        XCTAssertTrue(checkContinue.waitForExistence(timeout: timeout))
        XCTAssertFalse(checkContinue.isEnabled, "The case must stay locked until every concept check is answered")

        let answers = [
            ("check-auth-record", "observation"),
            ("check-phishing", "inference"),
            ("check-process", "observation"),
            ("check-scope", "inference")
        ]
        for (checkID, answer) in answers {
            let choice = app.buttons["lab-check-\(checkID)-\(answer)"]
            reveal(choice, in: labScroll)
            choice.tap()
        }

        reveal(checkContinue, in: labScroll)
        XCTAssertTrue(checkContinue.isEnabled)
        checkContinue.tap()

        let authSource = app.buttons["lab-source-authentication"]
        reveal(authSource, in: labScroll)
        authSource.tap()

        let detailsButton = app.buttons.matching(
            NSPredicate(format: "label CONTAINS %@", "View Details")
        ).firstMatch
        reveal(detailsButton, in: labScroll)
        detailsButton.tap()
        let authDetails = app.staticTexts.containing(
            NSPredicate(format: "label CONTAINS %@", "underlying vendor event IDs")
        ).firstMatch
        XCTAssertTrue(authDetails.waitForExistence(timeout: timeout), "Inspecting the gateway record should expose its limits")

        let authPin = app.buttons["lab-pin-auth-1"]
        reveal(authPin, in: labScroll)
        authPin.tap()
        XCTAssertTrue(authPin.label.localizedCaseInsensitiveContains("Pinned"))

        let endpointSource = app.buttons["lab-source-endpoint"]
        reveal(endpointSource, in: labScroll)
        endpointSource.tap()
        let endpointPin = app.buttons["lab-pin-ep-2"]
        reveal(endpointPin, in: labScroll)
        endpointPin.tap()
        XCTAssertTrue(endpointPin.label.localizedCaseInsensitiveContains("Pinned"))

        let scopeQuery = app.buttons["lab-query-query-user"]
        reveal(scopeQuery, in: labScroll)
        scopeQuery.tap()
        let scopeResult = app.staticTexts.containing(
            NSPredicate(format: "label CONTAINS %@", "No reliable session ID links them")
        ).firstMatch
        XCTAssertTrue(scopeResult.waitForExistence(timeout: timeout), "The selected query should show its bounded result")

        let response = app.buttons["lab-response-isolate"]
        reveal(response, in: labScroll)
        response.tap()
        let simulationStatus = app.staticTexts.containing(
            NSPredicate(format: "label CONTAINS %@", "No endpoint action occurred")
        ).firstMatch
        reveal(simulationStatus, in: labScroll)

        let noteEditor = app.textViews["lab-handoff-note"]
        reveal(noteEditor, in: labScroll)
        noteEditor.tap()
        let marker = "uitest" + UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        noteEditor.typeText("Preserve FIN-17 evidence before isolation. \(marker)")
        XCTAssertTrue((noteEditor.value as? String ?? "").contains(marker), "The handoff note should accept typed investigation findings")

        let submit = app.buttons["lab-submit-handoff"]
        XCTAssertTrue(submit.isEnabled, "Pinned evidence, a scope query, a response, and a note should enable submission")
        submit.tap()

        XCTAssertTrue(app.staticTexts["Investigation Debrief"].waitForExistence(timeout: timeout))
        XCTAssertTrue(app.staticTexts["What the supplied records show"].exists)
        XCTAssertTrue(app.staticTexts["Isolation request selected"].exists)
        let recordedNote = app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", marker)).firstMatch
        reveal(recordedNote, in: labScroll)
        XCTAssertTrue(recordedNote.exists, "The debrief should retain the submitted handoff note")
        XCTAssertTrue(
            app.descendants(matching: .any)
                .matching(NSPredicate(format: "label CONTAINS %@", "Completed")).firstMatch.exists,
            "Submitting the handoff should mark the lab complete"
        )

        let exitLab = app.buttons["lab-exit-button"]
        XCTAssertTrue(exitLab.waitForExistence(timeout: timeout))
        exitLab.tap()
        XCTAssertTrue(openLab.waitForExistence(timeout: timeout))
        reveal(openLab, in: app.scrollViews.firstMatch)
        openLab.tap()

        XCTAssertTrue(labScroll.waitForExistence(timeout: timeout))
        XCTAssertTrue(app.staticTexts["Investigation Debrief"].waitForExistence(timeout: timeout))
        XCTAssertTrue(app.staticTexts["Isolation request selected"].exists,
                      "Reopening a completed lab should restore its submitted response evaluation")
        XCTAssertTrue(recordedNote.exists, "Reopening the debrief should retain the submitted handoff note")

        let replay = app.buttons["lab-replay-button"]
        reveal(replay, in: labScroll)
        replay.tap()
        let replaySubmit = app.buttons["lab-submit-handoff"]
        XCTAssertTrue(replaySubmit.waitForExistence(timeout: timeout))
        XCTAssertFalse(replaySubmit.isEnabled, "Replay should clear the decisions needed for another handoff")
        reveal(authSource, in: labScroll)
        authSource.tap()
        reveal(authPin, in: labScroll)
        XCTAssertFalse(authPin.label.localizedCaseInsensitiveContains("Pinned"), "Replay should clear pinned evidence")

        exitLab.tap()
        XCTAssertTrue(openLab.waitForExistence(timeout: timeout))
        reveal(openLab, in: app.scrollViews.firstMatch)
        openLab.tap()

        XCTAssertTrue(labScroll.waitForExistence(timeout: timeout))
        XCTAssertTrue(app.buttons["lab-lesson-continue"].waitForExistence(timeout: timeout))
        XCTAssertFalse(app.staticTexts["Investigation Debrief"].exists,
                       "Reopening after replay must not restore the submitted debrief")
        XCTAssertFalse(app.staticTexts["Isolation request selected"].exists,
                       "Replay must clear the previously submitted response")
    }

    func testLearningLabRowAppearsOnlyWhileCySAPlusCourseIsSelected() {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launch()

        // Switch to a course other than CySA+ first, whatever the launch
        // default is, so the later switch to CySA+ is a real course change.
        selectCourse(in: app, matching: "course-card-", excluding: "course-card-cysa-plus/")
        XCTAssertTrue(app.buttons["today-learn-new"].waitForExistence(timeout: timeout),
                      "Today should show course actions for the selected non-CySA course")
        XCTAssertFalse(app.buttons["today-learning-lab"].exists,
                       "The CS0-004 lab row must be hidden while a non-CySA course is selected")

        selectCourse(in: app, matching: "course-card-cysa-plus/")
        XCTAssertTrue(app.buttons["today-learning-lab"].waitForExistence(timeout: timeout),
                      "Switching to the CySA+ course should reveal the CS0-004 lab row")
    }

    /// Selects a course from Your courses by card identifier prefix.
    ///
    /// A card identifier is `course-card-<courseID>/<packID>`, so a course is
    /// addressed by its installed course identity — never by its title text
    /// and without guessing its pack ID.
    private func selectCourse(in app: XCUIApplication, matching prefix: String, excluding excludedPrefix: String? = nil) {
        let changeCourse = app.buttons["today-change-course"]
        XCTAssertTrue(changeCourse.waitForExistence(timeout: timeout), "Today header course control is missing")
        changeCourse.tap()

        let cardPredicate: NSPredicate
        if let excludedPrefix {
            cardPredicate = NSPredicate(
                format: "identifier BEGINSWITH %@ AND NOT identifier BEGINSWITH %@",
                prefix,
                excludedPrefix
            )
        } else {
            cardPredicate = NSPredicate(format: "identifier BEGINSWITH %@", prefix)
        }
        let card = app.buttons.matching(cardPredicate).firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: timeout), "No installed course card matches \(prefix)")
        card.tap()

        XCTAssertTrue(app.buttons["today-change-course"].waitForExistence(timeout: timeout),
                      "Selecting a course should return to Today")
    }

    private func reveal(_ element: XCUIElement, in scrollView: XCUIElement) {
        XCTAssertTrue(element.waitForExistence(timeout: timeout), "Missing control: \(element.identifier)")
        XCTAssertTrue(scrollView.waitForExistence(timeout: timeout), "The expected scroll view is missing")

        for _ in 0..<10 {
            if element.isHittable { return }
            if element.frame.maxY < scrollView.frame.minY {
                scrollView.swipeDown()
            } else {
                scrollView.swipeUp()
            }
        }
        XCTAssertTrue(element.isHittable, "Control is not reachable by scrolling: \(element.identifier)")
    }
}
