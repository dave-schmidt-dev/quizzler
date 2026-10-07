import Foundation

/// A value used for forward-compatible, non-content certification metadata.
/// Question text is represented only by `questions`; this value is never sent
/// across the CloudKit boundary.
public indirect enum JSONValue: Codable, Equatable, Sendable {
    case string(String)
    case number(Double)
    case boolean(Bool)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    public init(from decoder: Decoder) throws {
        if let c = try? decoder.singleValueContainer(), c.decodeNil() { self = .null; return }
        if let c = try? decoder.singleValueContainer(), let value = try? c.decode(String.self) { self = .string(value); return }
        if let c = try? decoder.singleValueContainer(), let value = try? c.decode(Bool.self) { self = .boolean(value); return }
        if let c = try? decoder.singleValueContainer(), let value = try? c.decode(Double.self) { self = .number(value); return }
        if let c = try? decoder.container(keyedBy: DynamicCodingKey.self) {
            var object: [String: JSONValue] = [:]
            for key in c.allKeys { object[key.stringValue] = try c.decode(JSONValue.self, forKey: key) }
            self = .object(object); return
        }
        var c = try decoder.unkeyedContainer(); var array: [JSONValue] = []
        while !c.isAtEnd { array.append(try c.decode(JSONValue.self)) }
        self = .array(array)
    }

    public func encode(to encoder: Encoder) throws {
        switch self {
        case .string(let v): var c = encoder.singleValueContainer(); try c.encode(v)
        case .number(let v): var c = encoder.singleValueContainer(); try c.encode(v)
        case .boolean(let v): var c = encoder.singleValueContainer(); try c.encode(v)
        case .object(let v): var c = encoder.container(keyedBy: DynamicCodingKey.self); for (key, value) in v { try c.encode(value, forKey: DynamicCodingKey(stringValue: key)!) }
        case .array(let v): var c = encoder.unkeyedContainer(); for value in v { try c.encode(value) }
        case .null: var c = encoder.singleValueContainer(); try c.encodeNil()
        }
    }
}

public struct CoverageEntry: Codable, Equatable, Sendable {
    public let topic: String
    public let objective: String?
    public let area: String?
    public let minimum: Int

    /// Topic and published-objective entries may use the same printable value
    /// without being duplicates; the authoring linter keeps those namespaces
    /// separate as well.
    var uniquenessKey: String {
        objective.map { "objective:\($0)" } ?? "topic:\(topic)"
    }
    public init(topic: String, area: String? = nil, minimum: Int = 1) {
        self.init(topic: topic, objective: nil, area: area, minimum: minimum)
    }

    private init(topic: String, objective: String?, area: String?, minimum: Int) {
        self.topic = topic
        self.objective = objective
        self.area = area
        self.minimum = minimum
    }

    enum CodingKeys: String, CodingKey, CaseIterable { case topic, objective, area, minimum = "min" }
    public init(from decoder: Decoder) throws {
        if let string = try? decoder.singleValueContainer().decode(String.self) {
            guard !string.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw QuestionDecodingError.malformedMetadata }
            self.init(topic: string); return
        }
        let allKeys = try decoder.container(keyedBy: DynamicCodingKey.self)
        guard Set(allKeys.allKeys.map(\.stringValue)).isSubset(of: Set(CodingKeys.allCases.map(\.stringValue))) else { throw QuestionDecodingError.malformedMetadata }
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let topic = try c.decodeIfPresent(String.self, forKey: .topic)
        let objective = try c.decodeIfPresent(String.self, forKey: .objective)
        guard (topic != nil) != (objective != nil) else { throw QuestionDecodingError.malformedMetadata }
        let key = topic ?? objective!
        let area = try c.decodeIfPresent(String.self, forKey: .area)
        let minimum = try c.decodeIfPresent(Int.self, forKey: .minimum) ?? 1
        guard !key.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, area?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty != true, minimum > 0 else { throw QuestionDecodingError.malformedMetadata }
        self.init(topic: key, objective: objective, area: area, minimum: minimum)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        if let objective { try c.encode(objective, forKey: .objective) } else { try c.encode(topic, forKey: .topic) }
        try c.encodeIfPresent(area, forKey: .area)
        if minimum != 1 { try c.encode(minimum, forKey: .minimum) }
    }
}

/// The top-level `partial_install` marker a quarantined pack carries: the
/// authored and installed counts, the removed question ids, and the SHA-256
/// of the quarantine sidecar record. `scripts/pack_quarantine.py` writes it
/// and lint L29 checks its shape, so decoding is strict (exactly these four
/// keys) and `PackManifest.validate()` fails closed on a malformed marker.
public struct PartialInstall: Codable, Equatable, Sendable {
    public let authoredCount: Int
    public let installedCount: Int
    public let quarantinedIDs: [String]
    public let recordDigest: String

    public init(authoredCount: Int, installedCount: Int, quarantinedIDs: [String], recordDigest: String) {
        self.authoredCount = authoredCount
        self.installedCount = installedCount
        self.quarantinedIDs = quarantinedIDs
        self.recordDigest = recordDigest
    }

    enum CodingKeys: String, CodingKey, CaseIterable {
        case authoredCount = "authored_count", installedCount = "installed_count", quarantinedIDs = "quarantined_ids", recordDigest = "record_digest"
    }

    public init(from decoder: Decoder) throws {
        let allKeys = try decoder.container(keyedBy: DynamicCodingKey.self)
        guard Set(allKeys.allKeys.map(\.stringValue)) == Set(CodingKeys.allCases.map(\.stringValue)) else { throw QuestionDecodingError.malformedMetadata }
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.authoredCount = try c.decode(Int.self, forKey: .authoredCount)
        self.installedCount = try c.decode(Int.self, forKey: .installedCount)
        self.quarantinedIDs = try c.decode([String].self, forKey: .quarantinedIDs)
        self.recordDigest = try c.decodeNonBlank(String.self, forKey: .recordDigest)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(authoredCount, forKey: .authoredCount); try c.encode(installedCount, forKey: .installedCount)
        try c.encode(quarantinedIDs, forKey: .quarantinedIDs); try c.encode(recordDigest, forKey: .recordDigest)
    }

    /// Enforces the marker rules `marker_shape_reasons` in
    /// `scripts/pack_quarantine.py` (mirrored by lint L29) checks: counts
    /// that match the installed questions, unique quarantined ids disjoint
    /// from them, and a lowercase `sha256:<hex>` record digest.
    func validate(installedQuestionIDs: [String]) throws {
        guard authoredCount >= 0, installedCount >= 0,
              installedCount == installedQuestionIDs.count,
              authoredCount > installedCount,
              quarantinedIDs.allSatisfy({ !$0.isBlank }),
              Set(quarantinedIDs).count == quarantinedIDs.count,
              quarantinedIDs.count == authoredCount - installedCount,
              Set(quarantinedIDs).isDisjoint(with: installedQuestionIDs),
              Self.isRecordDigest(recordDigest)
        else { throw QuestionDecodingError.malformedMetadata }
    }

    private static func isRecordDigest(_ value: String) -> Bool {
        let hex = value.dropFirst(7)
        return value.hasPrefix("sha256:") && hex.count == 64 && hex.allSatisfy { "0123456789abcdef".contains($0) }
    }
}

public struct PackManifest: Codable, Equatable, Sendable {
    public static let currentContractVersion = 1
    public let packID: String
    public let subject: String
    public let title: String
    public let version: Int
    public let generatedAt: String?
    public let generationMode: String?
    public let sourceRounds: [String]
    public let notes: String?
    public let coverageBlueprint: [CoverageEntry]?
    public let certification: [String: JSONValue]?
    public let partialInstall: PartialInstall?
    public let questions: [Question]

    public init(packID: String, subject: String, title: String, version: Int = 1,
                generatedAt: String? = nil, generationMode: String? = nil,
                sourceRounds: [String] = [], notes: String? = nil,
                coverageBlueprint: [CoverageEntry]? = nil,
                certification: [String: JSONValue]? = nil,
                partialInstall: PartialInstall? = nil, questions: [Question]) throws {
        self.packID = packID; self.subject = subject; self.title = title; self.version = version; self.generatedAt = generatedAt; self.generationMode = generationMode; self.sourceRounds = sourceRounds; self.notes = notes; self.coverageBlueprint = coverageBlueprint; self.certification = certification; self.partialInstall = partialInstall; self.questions = questions
        try validate()
    }

    enum CodingKeys: String, CodingKey, CaseIterable {
        case packID = "pack_id", subject, title, version, generatedAt = "generated_at", generationMode = "generation_mode", sourceRounds = "source_rounds", notes, coverageBlueprint = "coverage_blueprint", certification, partialInstall = "partial_install", lintWaivers = "lint_waivers", factcheckWaivers = "factcheck_waivers", sourceDirective = "source_directive", questions
    }

    public init(from decoder: Decoder) throws {
        let allKeys = try decoder.container(keyedBy: DynamicCodingKey.self)
        guard Set(allKeys.allKeys.map(\.stringValue)).isSubset(of: Set(CodingKeys.allCases.map(\.stringValue))) else { throw QuestionDecodingError.malformedMetadata }
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.packID = try c.decodeNonBlank(String.self, forKey: .packID)
        self.subject = try c.decodeNonBlank(String.self, forKey: .subject)
        self.title = try c.decodeNonBlank(String.self, forKey: .title)
        self.version = try c.decode(Int.self, forKey: .version)
        self.generatedAt = try c.decodeIfPresent(String.self, forKey: .generatedAt)
        self.generationMode = try c.decodeIfPresent(String.self, forKey: .generationMode)
        self.sourceRounds = try c.decodeIfPresent([String].self, forKey: .sourceRounds) ?? []
        self.notes = try c.decodeIfPresent(String.self, forKey: .notes)
        self.coverageBlueprint = try c.decodeIfPresent([CoverageEntry].self, forKey: .coverageBlueprint)
        self.certification = try c.decodeIfPresent([String: JSONValue].self, forKey: .certification)
        self.partialInstall = try c.decodeIfPresent(PartialInstall.self, forKey: .partialInstall)
        self.questions = try c.decode([Question].self, forKey: .questions)
        try validate()
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(packID, forKey: .packID); try c.encode(subject, forKey: .subject); try c.encode(title, forKey: .title); try c.encode(version, forKey: .version)
        try c.encodeIfPresent(generatedAt, forKey: .generatedAt); try c.encodeIfPresent(generationMode, forKey: .generationMode); if !sourceRounds.isEmpty { try c.encode(sourceRounds, forKey: .sourceRounds) }; try c.encodeIfPresent(notes, forKey: .notes); try c.encodeIfPresent(coverageBlueprint, forKey: .coverageBlueprint); try c.encodeIfPresent(certification, forKey: .certification); try c.encodeIfPresent(partialInstall, forKey: .partialInstall); try c.encode(questions, forKey: .questions)
    }

    public func validate() throws {
        guard version > 0, version == Self.currentContractVersion, !packID.isBlank, !subject.isBlank, !title.isBlank, !questions.isEmpty else { throw QuestionDecodingError.malformedMetadata }
        guard sourceRounds.allSatisfy({ !$0.isBlank }), notes?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty != true, notes?.count ?? 0 <= 120 else { throw QuestionDecodingError.malformedMetadata }
        if let generatedAt { guard ISO8601DateFormatter().date(from: generatedAt) != nil else { throw QuestionDecodingError.malformedMetadata } }
        if let generationMode { guard ["manual", "templated", "llm", "hybrid"].contains(generationMode) else { throw QuestionDecodingError.malformedMetadata } }
        if let coverageBlueprint { guard Set(coverageBlueprint.map(\.uniquenessKey)).count == coverageBlueprint.count else { throw QuestionDecodingError.malformedMetadata } }
        var ids = Set<String>()
        for question in questions {
            guard ids.insert(question.id).inserted else { throw QuestionDecodingError.duplicateQuestionID(question.id) }
            try question.validateStrict()
        }
        if let partialInstall { try partialInstall.validate(installedQuestionIDs: questions.map(\.id)) }
    }
}

private struct DynamicCodingKey: CodingKey { let stringValue: String; init?(stringValue: String) { self.stringValue = stringValue }; let intValue: Int? = nil; init?(intValue: Int) { return nil } }
private extension String { var isBlank: Bool { trimmingCharacters(in: .whitespacesAndNewlines).isEmpty } }
private extension KeyedDecodingContainer { func decodeNonBlank<T: Decodable>(_ type: T.Type, forKey key: Key) throws -> T { let value = try decode(type, forKey: key); if let value = value as? String, value.isBlank { throw QuestionDecodingError.malformedMetadata }; return value } }
