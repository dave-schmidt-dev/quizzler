import XCTest
@testable import QuizzleriOS

final class LabProgressStoreTests: XCTestCase {
    private func makeDefaults() throws -> (defaults: UserDefaults, suiteName: String) {
        let suiteName = "quizzler-lab-progress-store-tests-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suiteName))
        return (defaults, suiteName)
    }

    private func makeProgress(
        schemaVersion: Int = QuietPowerShellLabProgress.currentSchemaVersion,
        conceptSelections: [String: Bool] = ["check-auth-record": true],
        selectedSourceID: String = "endpoint",
        pinnedItemIDs: [String] = ["auth-1", "ep-2"],
        selectedResponseID: String? = "isolate",
        executedScopeQueryID: String? = "query-user",
        currentNote: String = "Preserve FIN-17 evidence before isolation."
    ) -> QuietPowerShellLabProgress {
        QuietPowerShellLabProgress(
            schemaVersion: schemaVersion,
            phase: .investigation,
            unlockedPhases: [.lesson, .check, .investigation],
            conceptSelections: conceptSelections,
            selectedSourceID: selectedSourceID,
            pinnedItemIDs: pinnedItemIDs,
            selectedResponseID: selectedResponseID,
            executedScopeQueryID: executedScopeQueryID,
            currentNote: currentNote
        )
    }

    func testSavedProgressRoundTripsThroughTheStore() throws {
        let (defaults, suiteName) = try makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let store = LabProgressStore(defaults: defaults)
        let progress = makeProgress()

        store.save(progress)

        XCTAssertEqual(store.load(), progress)
    }

    func testValidatedDropsIdentifiersThatAreNoLongerInTheCase() throws {
        let labCase = try QuietPowerShellCaseLoader.loadBundled()
        let knownCheckID = try XCTUnwrap(labCase.conceptChecks.first?.id)
        let knownSourceID = try XCTUnwrap(labCase.sources.first?.id)
        let knownEvidenceID = try XCTUnwrap(labCase.evidenceItems.first?.id)
        let progress = makeProgress(
            conceptSelections: [knownCheckID: true, "missing-check": false],
            selectedSourceID: "missing-source",
            pinnedItemIDs: [knownEvidenceID, "missing-evidence"],
            selectedResponseID: "missing-response",
            executedScopeQueryID: "missing-query"
        )

        let validated = try XCTUnwrap(progress.validated(against: labCase))

        XCTAssertEqual(validated.conceptSelections, [knownCheckID: true])
        XCTAssertEqual(validated.selectedSourceID, knownSourceID)
        XCTAssertEqual(validated.pinnedItemIDs, [knownEvidenceID])
        XCTAssertNil(validated.selectedResponseID)
        XCTAssertNil(validated.executedScopeQueryID)
        XCTAssertEqual(validated.currentNote, progress.currentNote)
    }

    func testValidatedRejectsAnUnsupportedSchemaVersion() throws {
        let labCase = try QuietPowerShellCaseLoader.loadBundled()
        let progress = makeProgress(schemaVersion: QuietPowerShellLabProgress.currentSchemaVersion + 1)

        XCTAssertNil(progress.validated(against: labCase))
    }

    func testCorruptStoredJSONLoadsAsNil() throws {
        let (defaults, suiteName) = try makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        defaults.set(Data("{ not valid json".utf8), forKey: LabProgressStore.storageKey)

        XCTAssertNil(LabProgressStore(defaults: defaults).load())
    }

    func testClearRemovesStoredProgress() throws {
        let (defaults, suiteName) = try makeDefaults()
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let store = LabProgressStore(defaults: defaults)
        store.save(makeProgress())
        XCTAssertNotNil(store.load())

        store.clear()

        XCTAssertNil(store.load())
        XCTAssertNil(defaults.object(forKey: LabProgressStore.storageKey))
    }
}
