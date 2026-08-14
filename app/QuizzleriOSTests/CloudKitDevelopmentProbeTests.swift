import XCTest

@MainActor
final class CloudKitDevelopmentProbeTests: XCTestCase {
    private let probeArgument = "--quizzler-development-cloudkit-probe"
    private let recoveryProbeArgument = "--quizzler-development-cloudkit-probe-recover"
    private let probeEnvironmentKey = "QUIZZLER_DEVELOPMENT_CLOUDKIT_PROBE"

    func testProbeIsDisabledWithoutExactOptIn() {
        let app = XCUIApplication()
        app.launch()

        XCTAssertFalse(app.staticTexts["cloudkit-development-probe-status"].exists)
        XCTAssertTrue(app.staticTexts["TODAY · SECURITY+"].waitForExistence(timeout: 5))
    }

    func testProbeInjectedEntitlementOrAccountFailureIsTerminalAndNonSuccess() {
        let app = XCUIApplication()
        app.launchArguments = [probeArgument]
        app.launchEnvironment[probeEnvironmentKey] = "enabled"
        app.launchEnvironment["QUIZZLER_DEVELOPMENT_CLOUDKIT_PROBE_INJECT_FAILURE"] =
            "unavailable_entitlement_or_account"
        app.launch()

        let status = app.staticTexts["cloudkit-development-probe-status"]
        XCTAssertTrue(status.waitForExistence(timeout: 5))
        let terminal = NSPredicate { _, _ in
            Self.decode(status.label)?.terminal == true
        }
        expectation(for: terminal, evaluatedWith: status)
        waitForExpectations(timeout: 10)

        guard let result = Self.decode(status.label) else {
            XCTFail("Injected probe failure was not machine-readable JSON")
            return
        }
        XCTAssertTrue(result.terminal)
        XCTAssertEqual(result.kind, "cloudkit_development_probe")
        XCTAssertEqual(result.status, "unavailable_entitlement_or_account")
        XCTAssertNotEqual(result.status, "complete")
    }

    func testProbeLifecycleIsOptInAndReportsMachineReadableTerminalResult() throws {
        try XCTSkipUnless(
            ProcessInfo.processInfo.environment["QUIZZLER_RUN_LIVE_CLOUDKIT_PROBE"] == "enabled",
            "Live Development CloudKit probe is opt-in for an attended physical-device run"
        )

        let app = XCUIApplication()
        app.launchArguments = [probeArgument]
        app.launchEnvironment[probeEnvironmentKey] = "enabled"
        app.launch()

        let status = app.staticTexts["cloudkit-development-probe-status"]
        XCTAssertTrue(status.waitForExistence(timeout: 5))
        let terminal = NSPredicate { _, _ in
            Self.decode(status.label)?.terminal == true
        }
        expectation(for: terminal, evaluatedWith: status)
        waitForExpectations(timeout: 180)

        guard let result = Self.decode(status.label) else {
            XCTFail("Probe status was not machine-readable JSON")
            return
        }
        XCTAssertTrue(result.terminal)
        XCTAssertEqual(result.kind, "cloudkit_development_probe")
        XCTAssertEqual(result.status, "complete", "Development probe failed: \(result.status)")
    }

#if DEBUG
    func testProbeRecoveryIsOptInAndReportsMachineReadableTerminalResult() throws {
        try XCTSkipUnless(
            ProcessInfo.processInfo.environment["QUIZZLER_RUN_LIVE_CLOUDKIT_PROBE_RECOVERY"] == "enabled",
            "Live Development CloudKit probe recovery is opt-in for an attended physical-device run"
        )

        let app = XCUIApplication()
        app.launchArguments = [recoveryProbeArgument]
        app.launchEnvironment[probeEnvironmentKey] = "enabled"
        app.launch()

        let status = app.staticTexts["cloudkit-development-probe-status"]
        XCTAssertTrue(status.waitForExistence(timeout: 5))
        let terminal = NSPredicate { _, _ in
            Self.decode(status.label)?.terminal == true
        }
        expectation(for: terminal, evaluatedWith: status)
        waitForExpectations(timeout: 180)

        guard let result = Self.decode(status.label) else {
            XCTFail("Recovery probe status was not machine-readable JSON")
            return
        }
        XCTAssertTrue(result.terminal)
        XCTAssertEqual(result.kind, "cloudkit_development_probe")
        XCTAssertEqual(result.status, "recovery_complete", "Recovery probe failed: \(result.status)")
    }
#endif

    private struct ProbeStatus: Decodable {
        let kind: String
        let status: String
        let terminal: Bool
    }

    private static func decode(_ line: String) -> ProbeStatus? {
        try? JSONDecoder().decode(ProbeStatus.self, from: Data(line.utf8))
    }

}
