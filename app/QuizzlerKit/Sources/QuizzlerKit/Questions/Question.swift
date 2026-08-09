import Foundation

/// The only question discriminators understood by the native client.
public enum QuestionType: String, Codable, CaseIterable, Sendable {
    case multipleChoice = "multiple_choice"
    case scenarioMultipleChoice = "scenario_multiple_choice"
    case multipleSelect = "multiple_select"
    case trueFalse = "true_false"
    case matching

    public var isInstallable: Bool {
        switch self {
        case .multipleChoice, .scenarioMultipleChoice, .multipleSelect: true
        case .trueFalse, .matching: false
        }
    }
}

public enum QuestionDifficulty: String, Codable, CaseIterable, Sendable {
    case easy
    case medium
    case hard
}

/// Stable, authoring-only fields shared by all question renderers.
public struct QuestionMetadata: Codable, Equatable, Sendable {
    public let topic: String
    public let examArea: String
    public let difficulty: QuestionDifficulty
    public let diagram: String?
    public let diagramAlt: String?
    public let tags: [String]

    public init(topic: String, examArea: String = "", difficulty: QuestionDifficulty,
                diagram: String? = nil, diagramAlt: String? = nil, tags: [String] = []) {
        self.topic = topic
        self.examArea = examArea
        self.difficulty = difficulty
        self.diagram = diagram
        self.diagramAlt = diagramAlt
        self.tags = tags
    }

    enum CodingKeys: String, CodingKey {
        case topic, examArea = "exam_area", difficulty, diagram, diagramAlt = "diagram_alt", tags
    }

    fileprivate func validate() throws {
        guard !topic.isBlank, !examArea.isBlank else { throw QuestionDecodingError.malformedMetadata }
        guard tags.allSatisfy({ !$0.isBlank }) else { throw QuestionDecodingError.malformedMetadata }
        if diagram != nil && diagramAlt?.isBlank == true { throw QuestionDecodingError.malformedMetadata }
    }
}

public struct MultipleChoiceQuestion: Codable, Equatable, Sendable {
    public let id: String
    public let metadata: QuestionMetadata
    public let prompt: String
    public let explanation: String
    public let options: [String]
    public let answer: Int

    public init(id: String, metadata: QuestionMetadata, prompt: String, explanation: String,
                options: [String], answer: Int) {
        self.id = id; self.metadata = metadata; self.prompt = prompt; self.explanation = explanation
        self.options = options; self.answer = answer
    }

    public var type: QuestionType { .multipleChoice }
    enum CodingKeys: String, CodingKey, CaseIterable { case id, type, topic, examArea = "exam_area", difficulty, prompt, explanation, options, answer, diagram, diagramAlt = "diagram_alt", tags }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        try rejectUnknownKeys(c, allowed: CodingKeys.allCases)
        try requireType(c, .multipleChoice)
        self.id = try c.decodeNonBlank(String.self, forKey: .id)
        self.metadata = try decodeMetadata(c)
        self.prompt = try c.decodeNonBlank(String.self, forKey: .prompt)
        self.explanation = try c.decodeNonBlank(String.self, forKey: .explanation)
        self.options = try decodeOptions(c)
        self.answer = try c.decode(Int.self, forKey: .answer)
        try validateChoice(answer: answer, options: options, metadata: metadata)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id); try c.encode(type.rawValue, forKey: .type)
        try encodeMetadata(metadata, into: &c); try c.encode(prompt, forKey: .prompt); try c.encode(explanation, forKey: .explanation)
        try c.encode(options, forKey: .options); try c.encode(answer, forKey: .answer)
    }
}

public struct ScenarioMultipleChoiceQuestion: Codable, Equatable, Sendable {
    public let id: String
    public let metadata: QuestionMetadata
    public let prompt: String
    public let explanation: String
    public let options: [String]
    public let answer: Int
    public init(id: String, metadata: QuestionMetadata, prompt: String, explanation: String, options: [String], answer: Int) {
        self.id = id; self.metadata = metadata; self.prompt = prompt; self.explanation = explanation; self.options = options; self.answer = answer
    }
    public var type: QuestionType { .scenarioMultipleChoice }
    enum CodingKeys: String, CodingKey, CaseIterable { case id, type, topic, examArea = "exam_area", difficulty, prompt, explanation, options, answer, diagram, diagramAlt = "diagram_alt", tags }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self); try rejectUnknownKeys(c, allowed: CodingKeys.allCases); try requireType(c, .scenarioMultipleChoice)
        self.id = try c.decodeNonBlank(String.self, forKey: .id); self.metadata = try decodeMetadata(c); self.prompt = try c.decodeNonBlank(String.self, forKey: .prompt); self.explanation = try c.decodeNonBlank(String.self, forKey: .explanation); self.options = try decodeOptions(c); self.answer = try c.decode(Int.self, forKey: .answer); try validateChoice(answer: answer, options: options, metadata: metadata)
    }
    public func encode(to encoder: Encoder) throws { var c = encoder.container(keyedBy: CodingKeys.self); try c.encode(id, forKey: .id); try c.encode(type.rawValue, forKey: .type); try encodeMetadata(metadata, into: &c); try c.encode(prompt, forKey: .prompt); try c.encode(explanation, forKey: .explanation); try c.encode(options, forKey: .options); try c.encode(answer, forKey: .answer) }
}

public struct MultipleSelectQuestion: Codable, Equatable, Sendable {
    public let id: String; public let metadata: QuestionMetadata; public let prompt: String; public let explanation: String; public let options: [String]; public let answers: [Int]
    public init(id: String, metadata: QuestionMetadata, prompt: String, explanation: String, options: [String], answers: [Int]) { self.id = id; self.metadata = metadata; self.prompt = prompt; self.explanation = explanation; self.options = options; self.answers = answers }
    public var type: QuestionType { .multipleSelect }
    enum CodingKeys: String, CodingKey, CaseIterable { case id, type, topic, examArea = "exam_area", difficulty, prompt, explanation, options, answers, diagram, diagramAlt = "diagram_alt", tags }
    public init(from decoder: Decoder) throws { let c = try decoder.container(keyedBy: CodingKeys.self); try rejectUnknownKeys(c, allowed: CodingKeys.allCases); try requireType(c, .multipleSelect); self.id = try c.decodeNonBlank(String.self, forKey: .id); self.metadata = try decodeMetadata(c); self.prompt = try c.decodeNonBlank(String.self, forKey: .prompt); self.explanation = try c.decodeNonBlank(String.self, forKey: .explanation); self.options = try decodeOptions(c); self.answers = try c.decode([Int].self, forKey: .answers); try validateMultipleSelect(answers: answers, options: options, metadata: metadata) }
    public func encode(to encoder: Encoder) throws { var c = encoder.container(keyedBy: CodingKeys.self); try c.encode(id, forKey: .id); try c.encode(type.rawValue, forKey: .type); try encodeMetadata(metadata, into: &c); try c.encode(prompt, forKey: .prompt); try c.encode(explanation, forKey: .explanation); try c.encode(options, forKey: .options); try c.encode(answers, forKey: .answers) }
}

public struct TrueFalseQuestion: Codable, Equatable, Sendable {
    public let id: String; public let metadata: QuestionMetadata; public let prompt: String; public let explanation: String; public let answer: Bool
    public init(id: String, metadata: QuestionMetadata, prompt: String, explanation: String, answer: Bool) { self.id = id; self.metadata = metadata; self.prompt = prompt; self.explanation = explanation; self.answer = answer }
    public var type: QuestionType { .trueFalse }
    enum CodingKeys: String, CodingKey, CaseIterable { case id, type, topic, examArea = "exam_area", difficulty, prompt, explanation, answer, diagram, diagramAlt = "diagram_alt", tags }
    public init(from decoder: Decoder) throws { let c = try decoder.container(keyedBy: CodingKeys.self); try rejectUnknownKeys(c, allowed: CodingKeys.allCases); try requireType(c, .trueFalse); self.id = try c.decodeNonBlank(String.self, forKey: .id); self.metadata = try decodeMetadata(c); self.prompt = try c.decodeNonBlank(String.self, forKey: .prompt); self.explanation = try c.decodeNonBlank(String.self, forKey: .explanation); self.answer = try c.decode(Bool.self, forKey: .answer); try metadata.validate() }
    public func encode(to encoder: Encoder) throws { var c = encoder.container(keyedBy: CodingKeys.self); try c.encode(id, forKey: .id); try c.encode(type.rawValue, forKey: .type); try encodeMetadata(metadata, into: &c); try c.encode(prompt, forKey: .prompt); try c.encode(explanation, forKey: .explanation); try c.encode(answer, forKey: .answer) }
}

public struct MatchingQuestion: Codable, Equatable, Sendable {
    public let id: String; public let metadata: QuestionMetadata; public let prompt: String; public let explanation: String; public let leftItems: [String]; public let rightItems: [String]; public let correctPairs: [Int]
    public init(id: String, metadata: QuestionMetadata, prompt: String, explanation: String, leftItems: [String], rightItems: [String], correctPairs: [Int]) { self.id = id; self.metadata = metadata; self.prompt = prompt; self.explanation = explanation; self.leftItems = leftItems; self.rightItems = rightItems; self.correctPairs = correctPairs }
    public var type: QuestionType { .matching }
    enum CodingKeys: String, CodingKey, CaseIterable { case id, type, topic, examArea = "exam_area", difficulty, prompt, explanation, leftItems = "leftItems", rightItems = "rightItems", correctPairs = "correctPairs", diagram, diagramAlt = "diagram_alt", tags }
    public init(from decoder: Decoder) throws { let c = try decoder.container(keyedBy: CodingKeys.self); try rejectUnknownKeys(c, allowed: CodingKeys.allCases); try requireType(c, .matching); self.id = try c.decodeNonBlank(String.self, forKey: .id); self.metadata = try decodeMetadata(c); self.prompt = try c.decodeNonBlank(String.self, forKey: .prompt); self.explanation = try c.decodeNonBlank(String.self, forKey: .explanation); self.leftItems = try decodeStrings(c, key: .leftItems, minimum: 1); self.rightItems = try decodeStrings(c, key: .rightItems, minimum: 1); self.correctPairs = try c.decode([Int].self, forKey: .correctPairs); guard correctPairs.count == leftItems.count, correctPairs.allSatisfy({ $0 >= 0 && $0 < rightItems.count }) else { throw QuestionDecodingError.invalidAnswerIndex }; guard Set(rightItems).count == rightItems.count else { throw QuestionDecodingError.malformedMetadata }; try metadata.validate() }
    public func encode(to encoder: Encoder) throws { var c = encoder.container(keyedBy: CodingKeys.self); try c.encode(id, forKey: .id); try c.encode(type.rawValue, forKey: .type); try encodeMetadata(metadata, into: &c); try c.encode(prompt, forKey: .prompt); try c.encode(explanation, forKey: .explanation); try c.encode(leftItems, forKey: .leftItems); try c.encode(rightItems, forKey: .rightItems); try c.encode(correctPairs, forKey: .correctPairs) }
}

public enum Question: Codable, Equatable, Sendable {
    case multipleChoice(MultipleChoiceQuestion)
    case scenarioMultipleChoice(ScenarioMultipleChoiceQuestion)
    case multipleSelect(MultipleSelectQuestion)
    case trueFalse(TrueFalseQuestion)
    case matching(MatchingQuestion)

    public var id: String { switch self { case .multipleChoice(let q): q.id; case .scenarioMultipleChoice(let q): q.id; case .multipleSelect(let q): q.id; case .trueFalse(let q): q.id; case .matching(let q): q.id } }
    public var type: QuestionType { switch self { case .multipleChoice: .multipleChoice; case .scenarioMultipleChoice: .scenarioMultipleChoice; case .multipleSelect: .multipleSelect; case .trueFalse: .trueFalse; case .matching: .matching } }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: DiscriminatorCodingKey.self)
        let raw = try c.decode(String.self, forKey: .type)
        guard let type = QuestionType(rawValue: raw) else { throw QuestionDecodingError.unknownType(raw) }
        switch type { case .multipleChoice: self = .multipleChoice(try MultipleChoiceQuestion(from: decoder)); case .scenarioMultipleChoice: self = .scenarioMultipleChoice(try ScenarioMultipleChoiceQuestion(from: decoder)); case .multipleSelect: self = .multipleSelect(try MultipleSelectQuestion(from: decoder)); case .trueFalse: self = .trueFalse(try TrueFalseQuestion(from: decoder)); case .matching: self = .matching(try MatchingQuestion(from: decoder)) }
    }
    public func encode(to encoder: Encoder) throws { switch self { case .multipleChoice(let q): try q.encode(to: encoder); case .scenarioMultipleChoice(let q): try q.encode(to: encoder); case .multipleSelect(let q): try q.encode(to: encoder); case .trueFalse(let q): try q.encode(to: encoder); case .matching(let q): try q.encode(to: encoder) } }
}

public enum QuestionDecodingError: Error, Equatable, Sendable {
    case unknownType(String), invalidAnswerIndex, duplicateQuestionID(String), malformedMetadata, legacyTypeRequiresAllowlistedDigest, invalidLegacyAllowlist
}

private enum DiscriminatorCodingKey: String, CodingKey { case type }
private extension String { var isBlank: Bool { trimmingCharacters(in: .whitespacesAndNewlines).isEmpty } }
private func requireType<K: CodingKey>(_ c: KeyedDecodingContainer<K>, _ expected: QuestionType) throws { guard let raw = try c.decodeIfPresent(String.self, forKey: K(stringValue: "type")!) else { throw QuestionDecodingError.malformedMetadata }; guard raw == expected.rawValue else { throw QuestionDecodingError.unknownType(raw) } }
private func rejectUnknownKeys<K: CodingKey>(_ c: KeyedDecodingContainer<K>, allowed: [K]) throws { let allowed = Set(allowed.map(\.stringValue)); if c.allKeys.contains(where: { !allowed.contains($0.stringValue) }) { throw QuestionDecodingError.malformedMetadata } }
private func decodeMetadata<K: CodingKey>(_ c: KeyedDecodingContainer<K>) throws -> QuestionMetadata { let topic = try c.decodeNonBlank(String.self, forKey: K(stringValue: "topic")!); let area = try c.decodeIfPresent(String.self, forKey: K(stringValue: "exam_area")!) ?? ""; let difficulty = try c.decode(QuestionDifficulty.self, forKey: K(stringValue: "difficulty")!); let diagram = try c.decodeIfPresent(String.self, forKey: K(stringValue: "diagram")!); let alt = try c.decodeIfPresent(String.self, forKey: K(stringValue: "diagram_alt")!); let tags = try c.decodeIfPresent([String].self, forKey: K(stringValue: "tags")!) ?? []; let m = QuestionMetadata(topic: topic, examArea: area, difficulty: difficulty, diagram: diagram, diagramAlt: alt, tags: tags); try m.validate(); return m }
private func encodeMetadata<K: CodingKey>(_ m: QuestionMetadata, into c: inout KeyedEncodingContainer<K>) throws { try c.encode(m.topic, forKey: K(stringValue: "topic")!); if !m.examArea.isEmpty { try c.encode(m.examArea, forKey: K(stringValue: "exam_area")!) }; try c.encode(m.difficulty, forKey: K(stringValue: "difficulty")!); try c.encodeIfPresent(m.diagram, forKey: K(stringValue: "diagram")!); try c.encodeIfPresent(m.diagramAlt, forKey: K(stringValue: "diagram_alt")!); if !m.tags.isEmpty { try c.encode(m.tags, forKey: K(stringValue: "tags")!) } }
private func decodeOptions<K: CodingKey>(_ c: KeyedDecodingContainer<K>) throws -> [String] { try decodeStrings(c, key: K(stringValue: "options")!, minimum: 2) }
private func decodeStrings<K: CodingKey>(_ c: KeyedDecodingContainer<K>, key: K, minimum: Int) throws -> [String] { let values = try c.decode([String].self, forKey: key); guard values.count >= minimum, values.allSatisfy({ !$0.isBlank }) else { throw QuestionDecodingError.malformedMetadata }; return values }
private func validateChoice(answer: Int, options: [String], metadata: QuestionMetadata) throws { guard options.count >= 2, options.indices.contains(answer) else { throw QuestionDecodingError.invalidAnswerIndex }; try metadata.validate() }
private func validateMultipleSelect(answers: [Int], options: [String], metadata: QuestionMetadata) throws { guard answers.count >= 2, Set(answers).count == answers.count, answers.allSatisfy({ options.indices.contains($0) }) else { throw QuestionDecodingError.invalidAnswerIndex }; try metadata.validate() }
private extension KeyedDecodingContainer { func decodeNonBlank<T: Decodable>(_ type: T.Type, forKey key: Key) throws -> T { let value = try decode(type, forKey: key); if let value = value as? String, value.isBlank { throw QuestionDecodingError.malformedMetadata }; return value } }

/// A question ID is never meaningful without its pack (and course) tuple.
public struct QuestionIdentity: Codable, Hashable, Equatable, Sendable, CustomStringConvertible {
    public let courseID: String
    public let packID: String
    public let questionID: String
    public init(courseID: String, packID: String, questionID: String) {
        self.courseID = courseID; self.packID = packID; self.questionID = questionID
    }
    public var description: String { "\(courseID)::\(packID)::\(questionID)" }
    enum CodingKeys: String, CodingKey { case courseID = "course_id", packID = "pack_id", questionID = "question_id" }
}

public typealias PackQuestionID = QuestionIdentity
public typealias QuestionKey = QuestionIdentity
