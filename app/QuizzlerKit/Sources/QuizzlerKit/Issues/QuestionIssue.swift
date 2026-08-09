import Foundation

public enum QuestionIssueCategory: String, Codable, Sendable, CaseIterable {
    case incorrectAnswer = "incorrect_answer"
    case typo
    case brokenMedia = "broken_media"
    case other
}

/// A privacy-minimal queued report. It has identity and user-supplied category
/// context only; question text, answers, and pack contents never enter it.
public struct QuestionIssue: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let identity: QuestionIdentity
    public let category: QuestionIssueCategory
    public let note: String?
    public let createdAt: Date

    public var issueID: String { id }

    public init(
        issueID: String = UUID().uuidString.lowercased(),
        identity: QuestionIdentity,
        category: QuestionIssueCategory,
        note: String? = nil,
        createdAt: Date = Date()
    ) {
        precondition(!issueID.isEmpty, "issue IDs must not be empty")
        self.id = issueID
        self.identity = identity
        self.category = category
        self.note = note?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == true ? nil : note
        self.createdAt = createdAt
    }

    public init(issueID: String = UUID().uuidString.lowercased(), questionID: QuestionIdentity, category: QuestionIssueCategory, note: String? = nil, createdAt: Date = Date()) {
        self.init(issueID: issueID, identity: questionID, category: category, note: note, createdAt: createdAt)
    }
}
