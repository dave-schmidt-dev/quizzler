import XCTest

@MainActor
final class ColdLaunchStingUITests: XCTestCase {
    private let timeout: TimeInterval = 5

    override func tearDown() {
        XCUIApplication().terminate()
        super.tearDown()
    }

    /// The separate settled fixture proves the surface exists. A normal launch can finish
    /// before XCUITest returns from app.launch(), so assert the durable completion state here.
    func testColdLaunchStingCompletesAndDoesNotReplayOnForegroundReturn() {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] = "enabled"
        app.launchEnvironment["QUIZZLER_COLD_LAUNCH_STING"] = "enabled"
        app.launch()

        let sting = app.descendants(matching: .any)["launch.zero-delta-sting"]
        XCTAssertTrue(sting.waitForNonExistence(timeout: timeout), "Cold launch sting overlay should disappear after callback")

        let todayButton = app.buttons["Today"]
        XCTAssertTrue(todayButton.waitForExistence(timeout: timeout), "Normal app UI should remain after sting finishes")

        // A second foreground return must not show it.
        #if os(iOS) && !targetEnvironment(macCatalyst)
        XCUIDevice.shared.press(.home)
        #endif
        app.activate()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: timeout), "App should return to running foreground")

        XCTAssertFalse(sting.exists, "Cold launch sting must not replay on second foreground return")
        XCTAssertTrue(todayButton.exists, "Normal app UI should remain visible on foreground return")
    }

    /// Verifies the explicit settled sting UI test launch mode for reliable UI inspection and snapshot verification.
    func testSettledStingLaunchModePresentsWithoutDismissing() {
        let app = XCUIApplication()
        app.launchEnvironment["QUIZZLER_UI_TEST_LAUNCH_STING_SETTLED"] = "enabled"
        app.launch()

        let sting = app.descendants(matching: .any)["launch.zero-delta-sting"]
        XCTAssertTrue(sting.waitForExistence(timeout: timeout), "Settled sting fixture should appear")
        XCTAssertTrue(app.frame.contains(sting.frame), "Sting lockup should fit within app frame")
        XCTAssertGreaterThan(sting.frame.width, 0)
    }
}
