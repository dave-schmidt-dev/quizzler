import Foundation
import XCTest

@MainActor
final class CurriculumLabUITests: XCTestCase {
    private let timeout: TimeInterval = 8

    func testInvestigationCompletionPresentsDebriefAndReplayClearsCaseDecisions() {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launchEnvironment["QUIZZLER_UI_TEST_RESET_PREFERENCES"] = "enabled"
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

        let conceptTab = app.buttons["Concept check"]
        let investigationTab = app.buttons["Investigation"]
        let debriefTab = app.buttons["Debrief"]
        XCTAssertTrue(conceptTab.waitForExistence(timeout: timeout), "The phase bar should expose every lab phase")
        XCTAssertTrue(investigationTab.exists)
        XCTAssertTrue(debriefTab.exists)
        XCTAssertFalse(conceptTab.isEnabled, "The concept check unlocks only after the lesson continue")
        XCTAssertFalse(investigationTab.isEnabled, "Investigation unlocks only after the concept check")
        XCTAssertFalse(debriefTab.isEnabled, "Debrief unlocks only after the handoff is submitted")

        let lessonContinue = app.buttons["lab-lesson-continue"]
        reveal(lessonContinue, in: labScroll)
        lessonContinue.tap()

        let checkContinue = app.buttons["lab-concept-continue"]
        XCTAssertTrue(checkContinue.waitForExistence(timeout: timeout))
        XCTAssertFalse(checkContinue.isEnabled, "The case must stay locked until every concept check is answered")
        XCTAssertTrue(conceptTab.isEnabled, "Continuing the lesson unlocks the concept check tab")
        XCTAssertFalse(investigationTab.isEnabled, "Investigation stays locked until every concept check is answered")

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

        let pinnedSubmit = app.buttons["lab-submit-handoff"]
        XCTAssertTrue(pinnedSubmit.waitForExistence(timeout: timeout),
                      "The requirement checklist and Submit should be pinned during investigation")

        XCTAssertTrue(investigationTab.isEnabled, "Continuing the concept check unlocks the investigation tab")
        XCTAssertFalse(debriefTab.isEnabled, "The debrief tab stays locked until the handoff is submitted")

        let authSource = app.buttons["lab-source-authentication"]
        reveal(authSource, in: labScroll)
        authSource.tap()

        let detailsButton = app.buttons.matching(
            NSPredicate(format: "label CONTAINS %@", "View details")
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

        XCTAssertTrue(pinnedSubmit.isEnabled, "Pinned evidence, a scope query, a response, and a note should enable submission")
        XCTAssertTrue(pinnedSubmit.isHittable, "Submit should stay hittable without scrolling the investigation")
        pinnedSubmit.tap()

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
        let confirmReplay = app.buttons["Replay"].firstMatch
        XCTAssertTrue(confirmReplay.waitForExistence(timeout: timeout),
                      "Replay must confirm before clearing investigation decisions")
        let cancelReplay = app.buttons["Cancel"].firstMatch
        XCTAssertTrue(cancelReplay.exists, "The replay confirmation should offer a way to keep the debrief")
        cancelReplay.tap()
        XCTAssertTrue(confirmReplay.waitForNonExistence(timeout: timeout),
                      "The replay confirmation should dismiss after cancelling")
        XCTAssertTrue(app.staticTexts["Isolation request selected"].waitForExistence(timeout: timeout),
                      "Cancelling the replay confirmation should leave the submitted debrief intact")

        reveal(replay, in: labScroll)
        replay.tap()
        XCTAssertTrue(confirmReplay.waitForExistence(timeout: timeout))
        confirmReplay.tap()

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

    func testExitingAndReopeningTheLabRestoresPins() {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launchEnvironment["QUIZZLER_UI_TEST_RESET_PREFERENCES"] = "enabled"
        app.launch()

        selectCourse(in: app, matching: "course-card-cysa-plus/")

        let openLab = app.buttons["today-learning-lab"]
        XCTAssertTrue(openLab.waitForExistence(timeout: timeout))
        reveal(openLab, in: app.scrollViews.firstMatch)
        openLab.tap()

        let labScroll = app.scrollViews["lab-content-scroll"]
        XCTAssertTrue(labScroll.waitForExistence(timeout: timeout))

        let lessonContinue = app.buttons["lab-lesson-continue"]
        reveal(lessonContinue, in: labScroll)
        lessonContinue.tap()

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

        let checkContinue = app.buttons["lab-concept-continue"]
        reveal(checkContinue, in: labScroll)
        checkContinue.tap()

        XCTAssertTrue(app.otherElements["lab-investigation-bar"].waitForExistence(timeout: timeout),
                      "Continuing the concept check should open the investigation")

        let authSource = app.buttons["lab-source-authentication"]
        reveal(authSource, in: labScroll)
        authSource.tap()
        let authPin = app.buttons["lab-pin-auth-1"]
        reveal(authPin, in: labScroll)
        authPin.tap()

        let endpointSource = app.buttons["lab-source-endpoint"]
        reveal(endpointSource, in: labScroll)
        endpointSource.tap()
        let endpointPin = app.buttons["lab-pin-ep-2"]
        reveal(endpointPin, in: labScroll)
        endpointPin.tap()

        let exitLab = app.buttons["lab-exit-button"]
        XCTAssertTrue(exitLab.waitForExistence(timeout: timeout))
        exitLab.tap()

        XCTAssertTrue(openLab.waitForExistence(timeout: timeout))
        reveal(openLab, in: app.scrollViews.firstMatch)
        openLab.tap()

        XCTAssertTrue(labScroll.waitForExistence(timeout: timeout))
        XCTAssertTrue(app.otherElements["lab-investigation-bar"].waitForExistence(timeout: timeout),
                      "Reopening should restore the investigation phase")
        XCTAssertTrue(app.staticTexts["2+ source categories pinned (2/2)"].waitForExistence(timeout: timeout),
                      "Reopening should restore both pinned evidence items")

        let scopeQuery = app.buttons["lab-query-query-user"]
        reveal(scopeQuery, in: labScroll)
        scopeQuery.tap()
        let response = app.buttons["lab-response-isolate"]
        reveal(response, in: labScroll)
        response.tap()
        let noteEditor = app.textViews["lab-handoff-note"]
        reveal(noteEditor, in: labScroll)
        noteEditor.tap()
        noteEditor.typeText("Preserve FIN-17 evidence before isolation.")
        let submit = app.buttons["lab-submit-handoff"]
        XCTAssertTrue(submit.isEnabled, "The restored pins should help enable submission")
        XCTAssertTrue(submit.isHittable, "Submit should stay hittable without scrolling the investigation")
        submit.tap()

        XCTAssertTrue(app.staticTexts["Investigation Debrief"].waitForExistence(timeout: timeout))
        let replay = app.buttons["lab-replay-button"]
        reveal(replay, in: labScroll)
        replay.tap()
        let confirmReplay = app.buttons["Replay"].firstMatch
        XCTAssertTrue(confirmReplay.waitForExistence(timeout: timeout),
                      "Replay must confirm before clearing investigation decisions")
        confirmReplay.tap()

        XCTAssertTrue(app.staticTexts["2+ source categories pinned (0/2)"].waitForExistence(timeout: timeout),
                      "Replay should clear pinned evidence")
    }

    func testLearningLabRowAppearsOnlyWhileCySAPlusCourseIsSelected() {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launchEnvironment["QUIZZLER_UI_TEST_RESET_PREFERENCES"] = "enabled"
        app.launch()

        // Switch to a course other than CySA+ first, whatever the launch
        // default is, so the later switch to CySA+ is a real course change.
        selectCourse(in: app, matching: "course-card-", excluding: "course-card-cysa-plus/")
        XCTAssertTrue(app.buttons["today-hero-start"].waitForExistence(timeout: timeout),
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

        // The investigation pins its checklist bar over the scroll's bottom
        // edge (C16); a control under it reports hittable but is covered.
        // Short drags, because a full swipe can carry a control from under
        // the bar past the top and back again.
        for _ in 0..<20 {
            let bar = XCUIApplication().otherElements["lab-investigation-bar"]
            let visibleBottom = bar.exists ? min(bar.frame.minY, scrollView.frame.maxY) : scrollView.frame.maxY
            let midY = element.frame.midY
            if element.isHittable && midY > scrollView.frame.minY && midY < visibleBottom { return }
            let from = scrollView.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.6))
            let to = scrollView.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: midY <= scrollView.frame.minY ? 0.85 : 0.35))
            from.press(forDuration: 0.05, thenDragTo: to)
        }
        XCTAssertTrue(element.isHittable, "Control is not reachable by scrolling: \(element.identifier)")
    }
}
