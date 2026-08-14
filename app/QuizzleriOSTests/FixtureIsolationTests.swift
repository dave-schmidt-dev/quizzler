import XCTest

final class FixtureIsolationTests: XCTestCase {
    private var appRoot: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    }

    func testReleaseConfigurationExcludesFixtureSources() throws {
        let project = try String(contentsOf: appRoot.appendingPathComponent("project.yml"), encoding: .utf8)
        guard let release = project.range(of: "        Release:\n") else {
            XCTFail("project.yml has no Release configuration")
            return
        }
        let releaseSettings = project[release.lowerBound...]
        XCTAssertTrue(releaseSettings.contains("EXCLUDED_SOURCE_FILE_NAMES: \"*Fixture* *FailureInjection* *TestOnly*\""))
    }

    func testSeededFixtureIsDebugOnlyAndHasStableOptIn() throws {
        let fixtureURL = appRoot.appendingPathComponent("QuizzleriOS/TestingSupport/UITestFixture.swift")
        let fixture = try String(contentsOf: fixtureURL, encoding: .utf8)
        XCTAssertTrue(fixture.hasPrefix("import SwiftUI"))
        XCTAssertTrue(fixture.contains("#if DEBUG"))
        XCTAssertTrue(fixture.contains("QUIZZLER_UI_TEST_FIXTURE"))
        XCTAssertTrue(fixture.contains("Reports include question context only."))
        XCTAssertTrue(fixture.contains("Conflict detected"))
        XCTAssertTrue(fixture.contains("Offline recovery ready"))
    }

    func testDebugLaunchRoutingPreservesDevelopmentProbePrecedence() throws {
        let app = try String(
            contentsOf: appRoot.appendingPathComponent("QuizzleriOS/QuizzlerApp.swift"),
            encoding: .utf8
        )
        let debugRouting = try XCTUnwrap(app.components(separatedBy: "#if DEBUG").dropFirst().first)
        let probePosition = try XCTUnwrap(debugRouting.range(of: "DevelopmentProbeLaunch.mode")).lowerBound
        let fixturePosition = try XCTUnwrap(debugRouting.range(of: "UITestFixture.isEnabled")).lowerBound
        XCTAssertLessThan(probePosition, fixturePosition)
        XCTAssertTrue(debugRouting.contains("else {\n                LaunchpadView()"))
    }
}
