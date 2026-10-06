import XCTest
import QuizzlerKit
@testable import QuizzleriOS

/// Covers the three fixtures the study-preference UI tests rely on: the
/// launch-time preference reset, the pending-then-synced cloud script, and
/// the synthetic pack. None of these may touch a real learner's defaults,
/// progress, or course choice.
final class StudyPreferenceFixtureTests: XCTestCase {
    private let selectedPackKey = "StudyCatalog.selectedPackKey"
    private let resumePositionKey = "quizzler.study-resume-position.v1.synthetic.synthetic-core"
    private let labKey = "cysa004.lab.v1.quietpowershell.completed"
    private let survivorKey = "quizzler.unrelated-preference.v1"

    /// A private suite, so the reset tests never read or change the app
    /// host's real preferences.
    private func makeDefaults() throws -> (defaults: UserDefaults, suiteName: String) {
        let suiteName = "quizzler-study-preference-fixture-tests-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suiteName))
        return (defaults, suiteName)
    }

    private func seedPreferences(_ defaults: UserDefaults) {
        defaults.set(40, forKey: StudySessionLength.key)
        defaults.set(false, forKey: StudyScheduledReview.key)
        defaults.set("synthetic/synthetic-core", forKey: selectedPackKey)
        defaults.set(7, forKey: resumePositionKey)
        defaults.set(true, forKey: labKey)
        defaults.set("untouched", forKey: survivorKey)
    }

    func testResetLeavesPreferencesUntouchedWithoutTheResetEnvironment() throws {
        let (defaults, suiteName) = try makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        seedPreferences(defaults)

        UITestFixture.resetIsolatedPreferencesAtLaunch(
            environment: [UITestFixture.localProgressEnvironmentKey: UITestFixture.enabledValue],
            isRunningUnderXCTest: true,
            defaults: defaults
        )

        XCTAssertEqual(defaults.object(forKey: StudySessionLength.key) as? Int, 40)
        XCTAssertEqual(defaults.object(forKey: StudyScheduledReview.key) as? Bool, false)
        XCTAssertEqual(defaults.object(forKey: selectedPackKey) as? String, "synthetic/synthetic-core")
        XCTAssertEqual(defaults.object(forKey: resumePositionKey) as? Int, 7)
        XCTAssertEqual(defaults.object(forKey: labKey) as? Bool, true)
    }

    func testResetLeavesPreferencesUntouchedOutsideTheFixtures() throws {
        let (defaults, suiteName) = try makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        seedPreferences(defaults)

        UITestFixture.resetIsolatedPreferencesAtLaunch(
            environment: [UITestFixture.resetPreferencesEnvironmentKey: UITestFixture.enabledValue],
            isRunningUnderXCTest: false,
            defaults: defaults
        )

        XCTAssertEqual(defaults.object(forKey: StudySessionLength.key) as? Int, 40)
        XCTAssertEqual(defaults.object(forKey: StudyScheduledReview.key) as? Bool, false)
        XCTAssertEqual(defaults.object(forKey: selectedPackKey) as? String, "synthetic/synthetic-core")
        XCTAssertEqual(defaults.object(forKey: resumePositionKey) as? Int, 7)
        XCTAssertEqual(defaults.object(forKey: labKey) as? Bool, true)
    }

    func testResetRemovesExactlyTheIsolatedStudyPreferences() throws {
        let (defaults, suiteName) = try makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        seedPreferences(defaults)

        UITestFixture.resetIsolatedPreferencesAtLaunch(
            environment: [
                UITestFixture.localProgressEnvironmentKey: UITestFixture.enabledValue,
                UITestFixture.resetPreferencesEnvironmentKey: UITestFixture.enabledValue,
            ],
            isRunningUnderXCTest: true,
            defaults: defaults
        )

        XCTAssertNil(defaults.object(forKey: StudySessionLength.key))
        XCTAssertNil(defaults.object(forKey: StudyScheduledReview.key))
        XCTAssertNil(defaults.object(forKey: selectedPackKey))
        XCTAssertNil(defaults.object(forKey: resumePositionKey))
        XCTAssertNil(defaults.object(forKey: labKey))
        XCTAssertEqual(defaults.object(forKey: survivorKey) as? String, "untouched",
                       "the reset removed a preference it does not own")
    }

    func testPendingThenSyncedScriptFailsOnceThenSucceedsOnEveryRetry() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("quizzler-study-preference-fixture-tests-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let repository = CloudStatusFixtureProgressRepository(
            actorID: "test-actor-pending-then-synced",
            store: LocalProgressStore(fileURL: directory.appendingPathComponent("progress.json")),
            script: .syncPendingThenSynced
        )

        // The launch baseline establishes the authoritative local state.
        try await repository.synchronize()
        let baseline = try await repository.snapshot()
        XCTAssertEqual(baseline.schemaVersion, ProgressEnvelope.currentSchemaVersion)

        // The first scripted send fails, which drives the model into
        // `.syncPending`.
        await XCTAssertThrowsErrorAsync(try await repository.synchronize()) { error in
            XCTAssertEqual(
                error as? CloudStatusFixtureProgressRepository.SynchronizeError,
                .scriptedSyncFailure
            )
        }

        // Every retry after it reaches the cloud.
        try await repository.synchronize()
        try await repository.synchronize()
    }

    func testSyntheticPackServesDistinctStableQuestionIDs() throws {
        let outcome = SyntheticStudyPack.makeLoader(count: 5)()

        XCTAssertEqual(outcome.failures, [])
        XCTAssertNil(outcome.loadError, "the synthetic pack was refused: \(String(describing: outcome.loadError))")
        let pack = try XCTUnwrap(outcome.packs.first)
        XCTAssertEqual(pack.courseID, SyntheticStudyPack.courseID)
        XCTAssertEqual(pack.packID, SyntheticStudyPack.packID)
        XCTAssertEqual(pack.subject, SyntheticStudyPack.subject)
        XCTAssertEqual(pack.questions.count, 5)
        XCTAssertEqual(
            pack.questions.map(\.id),
            ["syn-q001", "syn-q002", "syn-q003", "syn-q004", "syn-q005"]
        )
        XCTAssertEqual(Set(pack.questions.map(\.id)).count, 5, "the synthetic pack repeats a question ID")
    }

    func testSyntheticPackCountParsingAcceptsOnlyAPositiveInteger() {
        let key = UITestFixture.syntheticPackEnvironmentKey
        XCTAssertNil(SyntheticStudyPack.requestedQuestionCount(environment: [:]))
        XCTAssertNil(SyntheticStudyPack.requestedQuestionCount(environment: [key: "not-a-number"]))
        XCTAssertNil(SyntheticStudyPack.requestedQuestionCount(environment: [key: "0"]))
        XCTAssertNil(SyntheticStudyPack.requestedQuestionCount(environment: [key: "-3"]))
        XCTAssertEqual(SyntheticStudyPack.requestedQuestionCount(environment: [key: "45"]), 45)
    }

    func testSyntheticSelectionStoreIsInMemory() {
        let store = SyntheticStudyPack.makeSelectionStore()
        XCTAssertNil(store.selectedPackKey, "a fresh synthetic selection store is not empty")

        store.selectedPackKey = "\(SyntheticStudyPack.courseID)/\(SyntheticStudyPack.packID)"
        XCTAssertEqual(store.selectedPackKey, "synthetic/synthetic-core")

        let fresh = SyntheticStudyPack.makeSelectionStore()
        XCTAssertNil(fresh.selectedPackKey, "the synthetic selection store persisted a choice")
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
