import XCTest

final class AccessibilityUITests: XCTestCase {
    private let timeout: TimeInterval = 5

    func testLaunchpadExposesCoreNavigationLabelsAndControls() {
        let app = XCUIApplication()
        app.launch()

        for label in ["Today", "Question", "Feedback", "Results", "Progress", "Settings"] {
            let navigationButton = app.buttons[label]
            XCTAssertTrue(navigationButton.waitForExistence(timeout: timeout), "Missing navigation control: \(label)")
            XCTAssertTrue(navigationButton.isHittable, "Navigation control is not tappable without scrolling: \(label)")
        }
        XCTAssertTrue(app.buttons["Start review"].waitForExistence(timeout: timeout))
        XCTAssertTrue(app.buttons["View progress"].waitForExistence(timeout: timeout))
    }

    func testProgressAndSettingsNavigationExposeAccessibleControls() {
        let app = XCUIApplication()
        app.launch()

        let viewProgress = app.buttons["View progress"]
        XCTAssertTrue(viewProgress.waitForExistence(timeout: timeout))
        viewProgress.tap()
        XCTAssertTrue(app.staticTexts["PROGRESS"].waitForExistence(timeout: timeout))
        XCTAssertTrue(app.switches["Shared progress"].waitForExistence(timeout: timeout))

        let settings = app.buttons["Settings"]
        XCTAssertTrue(settings.waitForExistence(timeout: timeout))
        XCTAssertTrue(settings.isHittable, "Settings must be tappable without scrolling")
        settings.tap()
        XCTAssertTrue(app.switches["Shared progress"].waitForExistence(timeout: timeout))
    }
}
