import Foundation
import QuizzlerKit

// MARK: - Versioned curriculum content

enum LabPhase: String, CaseIterable, Identifiable {
    case lesson = "Lesson", check = "Concept Check", investigation = "Investigation", debrief = "Debrief"
    var id: String { rawValue }
}

struct LabEvidenceSource: Codable, Identifiable, Equatable {
    let id: String
    let title: String

    var icon: String {
        switch id {
        case "authentication": return "person.badge.key"
        case "endpoint": return "desktopcomputer"
        case "dns": return "network"
        case "inventory": return "server.rack"
        default: return "doc.text.magnifyingglass"
        }
    }
}

struct EvidenceItem: Codable, Identifiable, Equatable {
    let id: String
    let sourceID: String
    let timestamp: String
    let host: String
    let summary: String
    let details: String
    let isDistractor: Bool
    let isUnavailableAfterReimage: Bool
    let appearsAfterDismissal: Bool
}

struct LabResponseChoice: Codable, Identifiable, Equatable {
    let id: String
    let title: String
    let consequenceSummary: String
    let statusMessage: String
    let impact: String
}

struct ScopeQueryOption: Codable, Identifiable, Equatable {
    let id: String
    let query: String
    let label: String
    let result: String
}

struct ConceptCheckItem: Codable, Identifiable, Equatable {
    let id: String
    let statement: String
    let classification: String
    let explanation: String

    var isObservation: Bool { classification == "observation" }
}

struct LessonPrinciple: Codable, Identifiable, Equatable {
    let id: String
    let title: String
    let body: String
}

struct LabObjectiveReference: Codable, Identifiable, Equatable {
    let id: String
    let kind: String
    let title: String
    let url: String
}

struct LabResponseEvaluation: Codable, Identifiable, Equatable {
    let responseID: String
    let heading: String
    let explanation: String
    var id: String { responseID }
}

struct LabDebrief: Codable, Equatable {
    let evidenceTitle: String
    let evidencePoints: [String]
    let scopeTitle: String
    let scopePoints: [String]
    let responseTitle: String
    let distractorTitle: String
    let distractorSummary: String
    let responseEvaluations: [LabResponseEvaluation]
}

struct QuietPowerShellCase: Codable, Equatable {
    let id: String
    let synthetic: Bool
    let title: String
    let lessonTitle: String
    let lessonIntro: String
    let lessonPrinciples: [LessonPrinciple]
    let checkTitle: String
    let checkPrompt: String
    let conceptChecks: [ConceptCheckItem]
    let caseTitle: String
    let caseDescription: String
    let objectiveReferences: [LabObjectiveReference]
    let sources: [LabEvidenceSource]
    let evidenceItems: [EvidenceItem]
    let scopeTitle: String
    let scopeQueries: [ScopeQueryOption]
    let responseTitle: String
    let responses: [LabResponseChoice]
    let reimageEvidenceLossNote: String
    let debrief: LabDebrief
}

enum QuietPowerShellCaseLoadError: Error, LocalizedError, Equatable {
    case missingResource
    case unreadableResource
    case invalid(String)

    var errorDescription: String? {
        switch self {
        case .missingResource: return "The CS0-004 practice case resource is missing from this app build."
        case .unreadableResource: return "The CS0-004 practice case resource could not be read."
        case .invalid(let reason): return "The CS0-004 practice case is unavailable because its data failed validation: \(reason)"
        }
    }
}

enum QuietPowerShellCaseLoader {
    private struct Envelope: Codable {
        let schemaVersion: Int
        let caseVersion: String
        let sha256: String
        let payload: QuietPowerShellCase
    }

    static func loadBundled(bundle: Bundle = .main) throws -> QuietPowerShellCase {
        guard let url = bundle.url(forResource: "QuietPowerShellCase-v1", withExtension: "json") else {
            throw QuietPowerShellCaseLoadError.missingResource
        }
        do { return try load(data: Data(contentsOf: url)) }
        catch let error as QuietPowerShellCaseLoadError { throw error }
        catch { throw QuietPowerShellCaseLoadError.unreadableResource }
    }

    static func load(data: Data) throws -> QuietPowerShellCase {
        let envelope: Envelope
        do { envelope = try JSONDecoder().decode(Envelope.self, from: data) }
        catch { throw QuietPowerShellCaseLoadError.invalid("malformed JSON or schema") }

        guard envelope.schemaVersion == 1 else { throw QuietPowerShellCaseLoadError.invalid("unsupported schema version") }
        guard nonblank(envelope.caseVersion), PackLoader.isDigest(envelope.sha256) else {
            throw QuietPowerShellCaseLoadError.invalid("missing case version or SHA-256 digest")
        }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        let canonicalPayload: Data
        do { canonicalPayload = try encoder.encode(envelope.payload) }
        catch { throw QuietPowerShellCaseLoadError.invalid("case payload could not be encoded") }
        guard PackLoader.contentDigest(for: canonicalPayload) == envelope.sha256 else {
            throw QuietPowerShellCaseLoadError.invalid("SHA-256 digest mismatch")
        }
        try validate(envelope.payload)
        return envelope.payload
    }

    private static func validate(_ value: QuietPowerShellCase) throws {
        guard value.synthetic else { throw QuietPowerShellCaseLoadError.invalid("case must be marked synthetic") }
        let required = [value.id, value.title, value.lessonTitle, value.lessonIntro, value.checkTitle,
                        value.checkPrompt, value.caseTitle, value.caseDescription, value.scopeTitle,
                        value.responseTitle, value.reimageEvidenceLossNote, value.debrief.evidenceTitle,
                        value.debrief.scopeTitle, value.debrief.responseTitle, value.debrief.distractorTitle,
                        value.debrief.distractorSummary]
        guard required.allSatisfy(nonblank) else { throw QuietPowerShellCaseLoadError.invalid("required text is empty") }
        guard !value.lessonPrinciples.isEmpty, !value.conceptChecks.isEmpty, !value.objectiveReferences.isEmpty,
              !value.sources.isEmpty, !value.evidenceItems.isEmpty, !value.scopeQueries.isEmpty,
              !value.responses.isEmpty, !value.debrief.evidencePoints.isEmpty, !value.debrief.scopePoints.isEmpty,
              !value.debrief.responseEvaluations.isEmpty else {
            throw QuietPowerShellCaseLoadError.invalid("required content collection is empty")
        }
        guard unique(value.lessonPrinciples.map(\.id)), unique(value.conceptChecks.map(\.id)),
              unique(value.objectiveReferences.map(\.id)), unique(value.sources.map(\.id)),
              unique(value.evidenceItems.map(\.id)), unique(value.scopeQueries.map(\.id)),
              unique(value.responses.map(\.id)), unique(value.debrief.responseEvaluations.map(\.responseID)) else {
            throw QuietPowerShellCaseLoadError.invalid("duplicate content identifier")
        }
        var textValues = value.lessonPrinciples.flatMap { [$0.id, $0.title, $0.body] }
        textValues.append(contentsOf: value.conceptChecks.flatMap { [$0.id, $0.statement, $0.classification, $0.explanation] })
        textValues.append(contentsOf: value.objectiveReferences.flatMap { [$0.id, $0.kind, $0.title, $0.url] })
        textValues.append(contentsOf: value.sources.flatMap { [$0.id, $0.title] })
        textValues.append(contentsOf: value.evidenceItems.flatMap { [$0.id, $0.sourceID, $0.timestamp, $0.host, $0.summary, $0.details] })
        textValues.append(contentsOf: value.scopeQueries.flatMap { [$0.id, $0.query, $0.label, $0.result] })
        textValues.append(contentsOf: value.responses.flatMap { [$0.id, $0.title, $0.consequenceSummary, $0.statusMessage, $0.impact] })
        textValues.append(contentsOf: value.debrief.evidencePoints)
        textValues.append(contentsOf: value.debrief.scopePoints)
        textValues.append(contentsOf: value.debrief.responseEvaluations.flatMap { [$0.responseID, $0.heading, $0.explanation] })
        guard textValues.allSatisfy(nonblank) else { throw QuietPowerShellCaseLoadError.invalid("content contains blank text") }
        guard value.conceptChecks.allSatisfy({ ["observation", "inference"].contains($0.classification) }) else {
            throw QuietPowerShellCaseLoadError.invalid("unknown observation classification")
        }
        guard value.objectiveReferences.allSatisfy({ ["objective", "guidance"].contains($0.kind) }),
              value.responses.allSatisfy({ ["success", "warning", "danger", "neutral"].contains($0.impact) }) else {
            throw QuietPowerShellCaseLoadError.invalid("unknown objective or response kind")
        }
        let sourceIDs = Set(value.sources.map(\.id))
        guard value.evidenceItems.allSatisfy({ sourceIDs.contains($0.sourceID) }) else {
            throw QuietPowerShellCaseLoadError.invalid("evidence references an unknown source")
        }
        let responseIDs = Set(value.responses.map(\.id))
        guard value.debrief.responseEvaluations.allSatisfy({ responseIDs.contains($0.responseID) }),
              Set(value.debrief.responseEvaluations.map(\.responseID)) == responseIDs else {
            throw QuietPowerShellCaseLoadError.invalid("debrief response mapping is incomplete")
        }
        guard value.objectiveReferences.allSatisfy({ URL(string: $0.url)?.scheme == "https" }) else {
            throw QuietPowerShellCaseLoadError.invalid("objective source must use HTTPS")
        }
    }

    private static func nonblank(_ value: String) -> Bool {
        !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private static func unique<T: Hashable>(_ values: [T]) -> Bool {
        Set(values).count == values.count
    }
}
