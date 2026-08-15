import XCTest
import UIKit

@MainActor
final class AccessibilityUITests: XCTestCase {
    private let timeout: TimeInterval = 5

    private func fixture(dynamicType: Bool = false) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_FIXTURE"] = "enabled"
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
        app.launch()

        for label in ["Today", "Progress", "Settings"] {
            let navigationButton = app.buttons[label]
            XCTAssertTrue(navigationButton.waitForExistence(timeout: timeout), "Missing navigation control: \(label)")
            XCTAssertTrue(navigationButton.isHittable, "Navigation control is not tappable without scrolling: \(label)")
        }
        for workflowState in ["Question", "Feedback", "Results"] {
            XCTAssertFalse(app.buttons[workflowState].exists, "Workflow state must not be a persistent navigation control: \(workflowState)")
        }

        let startReview = app.buttons["Start review"]
        XCTAssertTrue(startReview.waitForExistence(timeout: timeout))
        XCTAssertTrue(app.buttons["View progress"].waitForExistence(timeout: timeout))
        startReview.tap()
        XCTAssertTrue(app.descendants(matching: .any)["question-shell"].waitForExistence(timeout: timeout))
    }

    func testProgressAndSettingsNavigationExposeAccessibleControls() {
        let app = XCUIApplication()
        app.launch()

        let viewProgress = app.buttons["View progress"]
        XCTAssertTrue(viewProgress.waitForExistence(timeout: timeout))
        viewProgress.tap()
        XCTAssertTrue(app.staticTexts["PROGRESS"].waitForExistence(timeout: timeout))
        XCTAssertTrue(
            app.staticTexts["Progress is stored locally on this device. Cloud sharing remains unavailable until Production qualification."]
                .waitForExistence(timeout: timeout)
        )

        let settings = app.buttons["Settings"]
        XCTAssertTrue(settings.waitForExistence(timeout: timeout))
        XCTAssertTrue(settings.isHittable, "Settings must be tappable without scrolling")
        settings.tap()
        XCTAssertTrue(app.staticTexts["Progress, Local only"].waitForExistence(timeout: timeout))
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
