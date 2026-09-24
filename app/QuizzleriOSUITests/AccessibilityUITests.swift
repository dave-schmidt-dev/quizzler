import XCTest
import UIKit

@MainActor
final class AccessibilityUITests: XCTestCase {
    private let timeout: TimeInterval = 5

    private func fixture(dynamicType: Bool = false) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_FIXTURE"] = "enabled"
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        if dynamicType {
            app.launchArguments += [
                "-UIPreferredContentSizeCategoryName",
                "UICTContentSizeCategoryAccessibilityXXXL"
            ]
        }
        app.launch()
        XCTAssertTrue(app.otherElements["fixture-root"].waitForExistence(timeout: timeout))
        return app
    }

    func testLaunchpadExposesCoreNavigationLabelsAndControls() {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launch()

        for label in ["Today", "Progress", "Settings"] {
            let navigationButton = app.buttons[label]
            XCTAssertTrue(navigationButton.waitForExistence(timeout: timeout), "Missing navigation control: \(label)")
            XCTAssertTrue(navigationButton.isHittable, "Navigation control is not tappable without scrolling: \(label)")
        }
        for workflowState in ["Question", "Feedback", "Results"] {
            XCTAssertFalse(app.buttons[workflowState].exists, "Workflow state must not be a persistent navigation control: \(workflowState)")
        }

        let heroStart = app.buttons["today-hero-start"]
        XCTAssertTrue(heroStart.waitForExistence(timeout: timeout))
        XCTAssertTrue(heroStart.isHittable, "Hero start button is not tappable without scrolling")
        let syncStatus = app.descendants(matching: .any)["global-progress-status"]
        XCTAssertTrue(syncStatus.waitForExistence(timeout: timeout))

        let courseControl = app.buttons["today-change-course"]
        XCTAssertTrue(courseControl.waitForExistence(timeout: timeout), "Today header course control is missing")
        XCTAssertTrue(courseControl.isHittable, "Today header course control is not hittable")
        XCTAssertLessThanOrEqual(courseControl.frame.maxX, syncStatus.frame.minX, "Header course control and sync badge overlap horizontally")
        XCTAssertLessThan(courseControl.frame.minY, syncStatus.frame.maxY, "Header course control and sync badge do not share row vertically")
        XCTAssertGreaterThan(courseControl.frame.maxY, syncStatus.frame.minY, "Header course control and sync badge do not share row vertically")

        heroStart.tap()
        XCTAssertTrue(app.descendants(matching: .any)["question-shell"].waitForExistence(timeout: timeout))
        XCTAssertTrue(app.descendants(matching: .any)["global-progress-status"].exists, "Question state lost the global sync status")
    }

    func testProgressAndSettingsNavigationExposeAccessibleControls() {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launch()

        let progress = app.buttons["Progress"]
        XCTAssertTrue(progress.waitForExistence(timeout: timeout))
        XCTAssertTrue(progress.isHittable, "Progress tab must be tappable without scrolling")
        progress.tap()
        XCTAssertFalse(app.navigationBars["Progress"].isHittable, "Progress has a redundant system navigation bar")
        XCTAssertTrue(app.descendants(matching: .any)["progress-coverage"].waitForExistence(timeout: timeout))
        XCTAssertTrue(app.descendants(matching: .any)["global-progress-status"].exists, "Progress lost the global sync status")

        let settings = app.buttons["Settings"]
        XCTAssertTrue(settings.waitForExistence(timeout: timeout))
        XCTAssertTrue(settings.isHittable, "Settings must be tappable without scrolling")
        settings.tap()
        XCTAssertFalse(app.navigationBars["Settings"].isHittable, "Settings has a redundant system navigation bar")
        XCTAssertFalse(app.descendants(matching: .any)["settings-progress-status"].exists, "Settings repeats the shared sync badge")
        XCTAssertTrue(app.descendants(matching: .any)["global-progress-status"].exists, "Settings lost the global sync status")

        let defaultLimit = app.descendants(matching: .any)["settings-default-session-limit"]
        XCTAssertTrue(defaultLimit.waitForExistence(timeout: timeout), "Study default limit is not reachable from Settings")
        XCTAssertTrue(defaultLimit.isHittable, "Study default limit is not tappable")

        let scheduledReview = app.switches["settings-scheduled-review"]
        XCTAssertTrue(scheduledReview.waitForExistence(timeout: timeout), "Scheduled review switch is not reachable from Settings")
        XCTAssertTrue(scheduledReview.isHittable, "Scheduled review switch is not tappable")

        let maximumLevel = app.descendants(matching: .any)["settings-maximum-leitner-level"]
        XCTAssertTrue(maximumLevel.waitForExistence(timeout: timeout), "Maximum Leitner level picker is missing")
        XCTAssertTrue(maximumLevel.isHittable, "Maximum Leitner level picker is not reachable")
        let pickerValue = (maximumLevel.value as? String).flatMap { $0.isEmpty ? nil : $0 }
        let pickerDescription = pickerValue ?? maximumLevel.label
        XCTAssertTrue(
            pickerDescription.localizedCaseInsensitiveContains("5"),
            "The picker does not expose the current synced level: \(maximumLevel.debugDescription)"
        )

        XCTAssertEqual(
            app.descendants(matching: .any).matching(identifier: "global-progress-status").count,
            1,
            "Settings must show exactly one shared sync indicator"
        )

        let versionValue = app.staticTexts["settings-app-version"]
        XCTAssertTrue(versionValue.waitForExistence(timeout: timeout), "Settings did not display App version and build")
        XCTAssertTrue(
            versionValue.label.range(of: #"^\d+\.\d+\.\d+ \(\d+\)$"#, options: .regularExpression) != nil,
            "Settings version is not a version/build pair: \(versionValue.label)"
        )
    }

    func testTodayExplanationLinkAndQuestionLevelHistoryControls() {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launch()

        let howReviewsWork = app.buttons["today-how-reviews-work"]
        XCTAssertTrue(howReviewsWork.waitForExistence(timeout: timeout))
        howReviewsWork.tap()
        XCTAssertTrue(app.navigationBars["How reviews work"].waitForExistence(timeout: timeout))
        let wikipediaLink = app.descendants(matching: .any)["scheduled-reviews-wikipedia-link"]
        XCTAssertTrue(wikipediaLink.exists, "The spaced repetition reference link is missing")
        XCTAssertTrue(wikipediaLink.label.localizedCaseInsensitiveContains("Spaced repetition on Wikipedia"))
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS[cd] %@", "session length is a maximum")).firstMatch.exists)
        app.buttons["scheduled-reviews-done"].tap()

        app.buttons["today-learn-new"].tap()
        let context = app.staticTexts["session-context"]
        XCTAssertTrue(context.waitForExistence(timeout: timeout))
        XCTAssertEqual(context.label, "Course study")
        XCTAssertTrue(app.descendants(matching: .any)["question-leitner-card"].waitForExistence(timeout: timeout))
        XCTAssertTrue(app.descendants(matching: .any)["question-leitner-pie"].exists)
        XCTAssertTrue(app.staticTexts["question-leitner-level"].exists)
        XCTAssertTrue(app.buttons["question-view-history"].exists)
        XCTAssertFalse(app.buttons["question-why-this"].exists, "Why this question belongs only to scheduled reviews")

        let exit = app.buttons["session-end"]
        let sync = app.descendants(matching: .any)["global-progress-status"]
        XCTAssertLessThanOrEqual(exit.frame.maxX, context.frame.minX, "Session header label overlaps Back to Today")
        XCTAssertLessThanOrEqual(context.frame.maxX, sync.frame.minX, "Session header label overlaps the sync badge")

        let questionScroll = app.scrollViews["question-shell"]
        let historyButton = app.buttons["question-view-history"]
        let reportButton = app.buttons["question-report"]
        XCTAssertTrue(questionScroll.exists)
        XCTAssertTrue(reportButton.exists)
        for _ in 0..<3 {
            if historyButton.isHittable && historyButton.frame.maxY <= reportButton.frame.minY {
                break
            }
            questionScroll.swipeUp()
        }
        XCTAssertTrue(historyButton.isHittable, "View history must be reachable by scrolling")
        XCTAssertLessThanOrEqual(historyButton.frame.maxY, reportButton.frame.minY)
        historyButton.tap()
        XCTAssertTrue(app.descendants(matching: .any)["question-history-sheet"].waitForExistence(timeout: timeout))
        XCTAssertTrue(app.descendants(matching: .any)["question-history-summary"].exists)
        XCTAssertTrue(app.descendants(matching: .any)["question-history-empty"].exists)
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "Earlier review history is unavailable")).firstMatch.exists)
        app.buttons["Done"].tap()
    }

    func testTodayHeaderCourseControlNavigatesToYourCourses() {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launch()

        let courseControl = app.buttons["today-change-course"]
        XCTAssertTrue(courseControl.waitForExistence(timeout: timeout), "Today header course control is missing")
        XCTAssertTrue(courseControl.isHittable, "Today header course control is not hittable")
        XCTAssertGreaterThanOrEqual(courseControl.frame.height, 44, "Today header course control is below the minimum touch target")
        XCTAssertGreaterThanOrEqual(courseControl.frame.width, 44, "Today header course control is below the minimum touch target")

        let syncBadge = app.descendants(matching: .any)["global-progress-status"]
        XCTAssertTrue(syncBadge.waitForExistence(timeout: timeout))
        XCTAssertLessThanOrEqual(courseControl.frame.maxX, syncBadge.frame.minX, "Header context and sync badge overlap")
        XCTAssertLessThan(courseControl.frame.minY, syncBadge.frame.maxY, "Header context and sync badge not in same row")
        XCTAssertGreaterThan(courseControl.frame.maxY, syncBadge.frame.minY, "Header context and sync badge not in same row")

        courseControl.tap()
        let courseHeading = app.staticTexts["courses-heading"]
        XCTAssertTrue(courseHeading.waitForExistence(timeout: timeout), "Course control did not navigate to Your courses")
        XCTAssertEqual(courseHeading.label, "Your courses")
        XCTAssertTrue(courseHeading.isHittable, "Your courses heading is not visible")
        XCTAssertFalse(app.navigationBars["Your courses"].isHittable, "Courses has a redundant system navigation bar")
        let backToToday = app.buttons["courses-back-to-today"]
        XCTAssertTrue(backToToday.waitForExistence(timeout: timeout), "Back to Today control is missing")
        XCTAssertEqual(app.buttons.matching(identifier: "courses-back-to-today").count, 1, "Courses has more than one Back to Today control")
        XCTAssertTrue(backToToday.isHittable, "Back to Today control is not hittable")
        XCTAssertGreaterThanOrEqual(backToToday.frame.height, 44, "Back to Today control is below the minimum touch target")
        XCTAssertGreaterThan(courseHeading.frame.minY, backToToday.frame.maxY, "Course heading overlaps the pinned header")
        XCTAssertLessThanOrEqual(backToToday.frame.maxX, syncBadge.frame.minX, "Back to Today control and sync badge overlap")
        XCTAssertLessThan(backToToday.frame.minY, syncBadge.frame.maxY, "Back to Today control and sync badge not in same row")
        XCTAssertGreaterThan(backToToday.frame.maxY, syncBadge.frame.minY, "Back to Today control and sync badge not in same row")

        backToToday.tap()
        XCTAssertTrue(app.buttons["today-change-course"].waitForExistence(timeout: timeout), "Back to Today did not return to Today")
        XCTAssertFalse(backToToday.exists, "Back to Today control remained after returning to Today")
    }

    func testFixtureVoiceOverLabelsAndFocusOrder() {
        let app = fixture()
        let title = app.staticTexts["fixture-title"]
        let today = app.staticTexts["Today"]
        let selectPack = app.buttons["Select pack"]
        XCTAssertTrue(title.exists && today.exists && selectPack.exists)
        XCTAssertLessThan(title.frame.minY, today.frame.minY)
        XCTAssertLessThan(today.frame.minY, selectPack.frame.minY)
        XCTAssertEqual(selectPack.label, "Select pack")
        XCTAssertEqual(app.buttons["Start review"].label, "Start review")
        XCTAssertEqual(app.buttons["Sync state"].label, "Sync state")
    }

    func testFixtureDynamicTypeKeepsLabelsAndControlsReachable() {
        let app = fixture(dynamicType: true)
        XCTAssertTrue(app.staticTexts["Deterministic offline study fixture"].waitForExistence(timeout: timeout))
        for label in ["Select pack", "Select mode", "Start review", "Sync state"] {
            let button = app.buttons[label]
            XCTAssertTrue(button.waitForExistence(timeout: timeout), "Missing Dynamic Type control: \(label)")
            XCTAssertTrue(button.isHittable, "Dynamic Type control is not hittable: \(label)")
        }
    }

    func testFixtureControlsMeetFortyFourPointTouchTarget() {
        let app = fixture()
        for label in ["Select pack", "Select mode", "Start review", "Sync state"] {
            let button = app.buttons[label]
            XCTAssertTrue(button.waitForExistence(timeout: timeout))
            XCTAssertGreaterThanOrEqual(button.frame.height, 44, "Touch target below 44pt: \(label)")
            XCTAssertGreaterThanOrEqual(button.frame.width, 44, "Touch target below 44pt: \(label)")
        }
    }

    func testFixtureSurvivesRotationOnEachSupportedDeviceClass() {
        let app = fixture()
        let idiom = UIDevice.current.userInterfaceIdiom
        XCTAssertTrue(idiom == .phone || idiom == .pad, "Unexpected UI test device class: \(idiom)")
        XCUIDevice.shared.orientation = .landscapeLeft
        XCTAssertTrue(app.otherElements["fixture-root"].waitForExistence(timeout: timeout))
        XCTAssertTrue(app.staticTexts["Today"].exists)
        XCUIDevice.shared.orientation = .portrait
        XCTAssertTrue(app.staticTexts["Today"].waitForExistence(timeout: timeout))
    }

}
