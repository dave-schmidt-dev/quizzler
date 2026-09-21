import XCTest
import QuizzlerKit
@testable import QuizzleriOS

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
        XCTAssertTrue(fixture.contains("QUIZZLER_UI_TEST_LOCAL_PROGRESS"))
        XCTAssertTrue(fixture.contains("XCTestConfigurationFilePath"))
        XCTAssertTrue(fixture.contains("Reports include question context only."))
        XCTAssertTrue(fixture.contains("Conflict detected"))
        XCTAssertTrue(fixture.contains("Offline recovery ready"))
    }

    func testDebugLaunchRoutingPreservesDevelopmentProbePrecedence() throws {
        let app = try String(
            contentsOf: appRoot.appendingPathComponent("QuizzleriOS/QuizzlerApp.swift"),
            encoding: .utf8
        )
        XCTAssertTrue(app.contains("private let progressRepository: (any LaunchpadProgressRepository)?"))
        XCTAssertTrue(app.contains("if DevelopmentProbeLaunch.mode != nil || UITestFixture.isEnabled"))
        XCTAssertTrue(app.contains("progressRepository = QuizzlerProgressRepository.debug()"))
        let bodyStart = try XCTUnwrap(app.range(of: "    var body: some Scene"))
        let bodyEnd = try XCTUnwrap(app.range(of: "\n    }\n}\n\nenum QuizzlerProgressRepository"))
        let debugRouting = String(app[bodyStart.lowerBound..<bodyEnd.lowerBound])
        let probePosition = try XCTUnwrap(debugRouting.range(of: "DevelopmentProbeLaunch.mode")).lowerBound
        let fixturePosition = try XCTUnwrap(debugRouting.range(of: "UITestFixture.isEnabled")).lowerBound
        XCTAssertLessThan(probePosition, fixturePosition)
        XCTAssertTrue(debugRouting.contains("else if let progressRepository"))
        XCTAssertFalse(debugRouting.contains("QuizzlerProgressRepository.localForUITest()"))
        XCTAssertFalse(debugRouting.contains("QuizzlerProgressRepository.production()"))
        XCTAssertTrue(app.contains("guard !UITestFixture.usesLocalProgress else { return true }"))
        XCTAssertTrue(debugRouting.contains("LaunchpadView(repository: progressRepository)"))
    }

    func testNoEnvironmentTestLaunchBuildsTheLocalRepository() {
        let repository = QuizzlerProgressRepository.debug(
            environment: [:],
            isRunningUnderXCTest: true
        )
        XCTAssertEqual(repository.syncMode, .local)
        XCTAssertTrue(UITestFixture.usesLocalProgress(environment: [:], isRunningUnderXCTest: true))
        XCTAssertFalse(UITestFixture.usesLocalProgress(environment: [:], isRunningUnderXCTest: false))
    }

    func testCloudStatusEnvironmentYieldsTheLocalBackedCloudFixture() throws {
        let repository = QuizzlerProgressRepository.debug(
            environment: ["QUIZZLER_UI_TEST_CLOUD_STATUS": "synced"],
            isRunningUnderXCTest: true
        )
        XCTAssertEqual(repository.syncMode, .cloudKit)
        XCTAssertTrue(repository is CloudStatusFixtureProgressRepository)
    }

    func testCloudStatusEnvironmentTakesPrecedenceOverLocalProgressEnvironment() throws {
        let repository = QuizzlerProgressRepository.debug(
            environment: [
                "QUIZZLER_UI_TEST_CLOUD_STATUS": "sync-pending",
                "QUIZZLER_UI_TEST_LOCAL_PROGRESS": "enabled",
            ],
            isRunningUnderXCTest: true
        )
        XCTAssertEqual(repository.syncMode, .cloudKit)
        XCTAssertTrue(repository is CloudStatusFixtureProgressRepository)
    }

    func testCloudStatusFixtureSynchronizeIsScriptedBySelectedStatus() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("quizzler-cloud-status-fixture-tests-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let synced = CloudStatusFixtureProgressRepository(
            actorID: "test-actor-synced",
            store: LocalProgressStore(fileURL: directory.appendingPathComponent("synced.json")),
            script: .synced
        )
        try await synced.synchronize()

        let syncPending = CloudStatusFixtureProgressRepository(
            actorID: "test-actor-sync-pending",
            store: LocalProgressStore(fileURL: directory.appendingPathComponent("sync-pending.json")),
            script: .syncPending
        )
        await XCTAssertThrowsErrorAsync(try await syncPending.synchronize()) { error in
            XCTAssertEqual(
                error as? CloudStatusFixtureProgressRepository.SynchronizeError,
                .scriptedSyncFailure
            )
        }
    }
}

private func XCTAssertThrowsErrorAsync<T: Sendable>(
    _ expression: @autoclosure () async throws -> T,
    _ errorHandler: (Error) -> Void = { _ in },
    file: StaticString = #filePath,
    line: UInt = #line
) async {
    do {
        _ = try await expression()
        XCTFail("expected an error to be thrown", file: file, line: line)
    } catch {
        errorHandler(error)
    }
}
