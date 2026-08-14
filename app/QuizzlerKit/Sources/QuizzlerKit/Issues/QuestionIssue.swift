import Foundation

/// Retained for the report form's presentation choices. The wire report uses
/// the documented description field instead of serializing this category.
public enum QuestionIssueCategory: String, Codable, Sendable, CaseIterable {
    case incorrectAnswer = "incorrect_answer"
    case typo
    case brokenMedia = "broken_media"
    case other
}

public enum QuestionIssueValidationError: Error, Equatable, Sendable {
    case blankField(String)
    case fieldTooLong(String)
    case unsupportedSchemaVersion(Int)
    case unknownField(String)
}

/// The privacy-minimal native issue-report payload defined by REPORT_SCHEMA v1.
/// It intentionally has no question text, answer bank, explanation, or
/// progress fields.
public struct QuestionIssue: Codable, Sendable, Equatable, Identifiable {
    public static let schemaVersion = 1
    public static let maxDescriptionLength = 2_000
    public static let maxSelectedResponseLength = 512
    private static let maxIdentifierLength = 256
    private static let maxVersionLength = 128

    public let issueID: String
    public let courseID: String
    public let packID: String
    public let questionID: String
    public let questionType: QuestionType
    public let appVersion: String
    public let build: String
    public let selectedResponse: String?
    public let description: String

    public var id: String { issueID }

    public init(
        issueID: String = "issue-\(UUID().uuidString.lowercased())",
        courseID: String,
        packID: String,
        questionID: String,
        questionType: QuestionType,
        appVersion: String,
        build: String,
        selectedResponse: String? = nil,
        description: String
    ) throws {
        let normalizedDescription = description.trimmingCharacters(in: .whitespacesAndNewlines)
        let normalizedSelectedResponse = selectedResponse?.trimmingCharacters(in: .whitespacesAndNewlines)
        try Self.validateNonBlank(issueID, field: "issue_id", maximumLength: Self.maxIdentifierLength)
        try Self.validateNonBlank(courseID, field: "course_id", maximumLength: Self.maxIdentifierLength)
        try Self.validateNonBlank(packID, field: "pack_id", maximumLength: Self.maxIdentifierLength)
        try Self.validateNonBlank(questionID, field: "question_id", maximumLength: Self.maxIdentifierLength)
        try Self.validateNonBlank(appVersion, field: "app_version", maximumLength: Self.maxVersionLength)
        try Self.validateNonBlank(build, field: "build", maximumLength: Self.maxVersionLength)
        try Self.validateNonBlank(normalizedDescription, field: "description", maximumLength: Self.maxDescriptionLength)
        if let normalizedSelectedResponse {
            try Self.validateNonBlank(normalizedSelectedResponse, field: "selected_response", maximumLength: Self.maxSelectedResponseLength)
        }

        self.issueID = issueID
        self.courseID = courseID
        self.packID = packID
        self.questionID = questionID
        self.questionType = questionType
        self.appVersion = appVersion
        self.build = build
        self.selectedResponse = normalizedSelectedResponse
        self.description = normalizedDescription
    }

    public init(from decoder: Decoder) throws {
        let rawContainer = try decoder.container(keyedBy: AnyCodingKey.self)
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let allowed = Set(CodingKeys.allCases.map(\.stringValue))
        if let unknown = rawContainer.allKeys.first(where: { !allowed.contains($0.stringValue) }) {
            throw QuestionIssueValidationError.unknownField(unknown.stringValue)
        }
        let schemaVersion = try container.decode(Int.self, forKey: .schemaVersion)
        guard schemaVersion == Self.schemaVersion else {
            throw QuestionIssueValidationError.unsupportedSchemaVersion(schemaVersion)
        }
        self.issueID = try container.decode(String.self, forKey: .issueID)
        self.courseID = try container.decode(String.self, forKey: .courseID)
        self.packID = try container.decode(String.self, forKey: .packID)
        self.questionID = try container.decode(String.self, forKey: .questionID)
        self.questionType = try container.decode(QuestionType.self, forKey: .questionType)
        self.appVersion = try container.decode(String.self, forKey: .appVersion)
        self.build = try container.decode(String.self, forKey: .build)
        self.selectedResponse = try container.decodeIfPresent(String.self, forKey: .selectedResponse)
        self.description = try container.decode(String.self, forKey: .description)
        try Self.validateNonBlank(issueID, field: "issue_id", maximumLength: Self.maxIdentifierLength)
        try Self.validateNonBlank(courseID, field: "course_id", maximumLength: Self.maxIdentifierLength)
        try Self.validateNonBlank(packID, field: "pack_id", maximumLength: Self.maxIdentifierLength)
        try Self.validateNonBlank(questionID, field: "question_id", maximumLength: Self.maxIdentifierLength)
        try Self.validateNonBlank(appVersion, field: "app_version", maximumLength: Self.maxVersionLength)
        try Self.validateNonBlank(build, field: "build", maximumLength: Self.maxVersionLength)
        try Self.validateNonBlank(description, field: "description", maximumLength: Self.maxDescriptionLength)
        if let selectedResponse {
            try Self.validateNonBlank(selectedResponse, field: "selected_response", maximumLength: Self.maxSelectedResponseLength)
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(Self.schemaVersion, forKey: .schemaVersion)
        try container.encode(issueID, forKey: .issueID)
        try container.encode(courseID, forKey: .courseID)
        try container.encode(packID, forKey: .packID)
        try container.encode(questionID, forKey: .questionID)
        try container.encode(questionType, forKey: .questionType)
        try container.encode(appVersion, forKey: .appVersion)
        try container.encode(build, forKey: .build)
        try container.encodeIfPresent(selectedResponse, forKey: .selectedResponse)
        try container.encode(description, forKey: .description)
    }

    private enum CodingKeys: String, CodingKey, CaseIterable {
        case schemaVersion = "schema_version"
        case issueID = "issue_id"
        case courseID = "course_id"
        case packID = "pack_id"
        case questionID = "question_id"
        case questionType = "question_type"
        case appVersion = "app_version"
        case build
        case selectedResponse = "selected_response"
        case description
    }

    private struct AnyCodingKey: CodingKey {
        let stringValue: String
        let intValue: Int?

        init?(stringValue: String) {
            self.stringValue = stringValue
            self.intValue = nil
        }

        init?(intValue: Int) {
            self.stringValue = String(intValue)
            self.intValue = intValue
        }
    }

    private static func validateNonBlank(_ value: String, field: String, maximumLength: Int) throws {
        guard !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw QuestionIssueValidationError.blankField(field)
        }
        guard value.count <= maximumLength else {
            throw QuestionIssueValidationError.fieldTooLong(field)
        }
    }
}
