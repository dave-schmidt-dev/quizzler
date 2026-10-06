import Foundation

struct QuietPowerShellLabProgress: Codable, Equatable {
    static let currentSchemaVersion = 1

    var schemaVersion: Int
    var phase: LabPhase
    var unlockedPhases: Set<LabPhase>
    var conceptSelections: [String: Bool]
    var selectedSourceID: String
    var pinnedItemIDs: [String]
    var selectedResponseID: String?
    var executedScopeQueryID: String?
    var currentNote: String

    func validated(against labCase: QuietPowerShellCase) -> QuietPowerShellLabProgress? {
        guard schemaVersion == Self.currentSchemaVersion else { return nil }

        let conceptIDs = Set(labCase.conceptChecks.map(\.id))
        let sourceIDs = Set(labCase.sources.map(\.id))
        let evidenceIDs = Set(labCase.evidenceItems.map(\.id))
        let responseIDs = Set(labCase.responses.map(\.id))
        let queryIDs = Set(labCase.scopeQueries.map(\.id))

        var checked = self
        checked.conceptSelections = conceptSelections.filter { conceptIDs.contains($0.key) }
        checked.pinnedItemIDs = pinnedItemIDs.filter { evidenceIDs.contains($0) }.sorted()
        if !sourceIDs.contains(selectedSourceID) {
            checked.selectedSourceID = labCase.sources.first?.id ?? ""
        }
        if let selectedResponseID, !responseIDs.contains(selectedResponseID) {
            checked.selectedResponseID = nil
        }
        if let executedScopeQueryID, !queryIDs.contains(executedScopeQueryID) {
            checked.executedScopeQueryID = nil
        }
        return checked
    }
}

struct LabProgressStore {
    static let storageKey = "cysa004.lab.v1.quietpowershell.in_progress"

    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    func load() -> QuietPowerShellLabProgress? {
        guard let data = defaults.data(forKey: Self.storageKey) else { return nil }
        return try? JSONDecoder().decode(QuietPowerShellLabProgress.self, from: data)
    }

    func save(_ progress: QuietPowerShellLabProgress) {
        guard let data = try? JSONEncoder().encode(progress) else { return }
        defaults.set(data, forKey: Self.storageKey)
    }

    func clear() {
        defaults.removeObject(forKey: Self.storageKey)
    }
}
