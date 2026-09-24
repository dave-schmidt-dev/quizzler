import Foundation

/// The states a local mutation can expose to the UI and to a sync adapter.
public enum ProgressStatus: String, Codable, Sendable, Equatable {
    case pending
    case applied
    case conflict
    case rebaseRequired = "rebase_required"
    case encodedSizeRefused = "encoded_size_refused"
    case offline
    case corruptState = "corrupt_state"
    case failed
}

/// The durable payload carried by a progress operation. New kinds must remain
/// explicit so an older writer rejects a newer envelope instead of silently
/// dropping synchronized state.
public enum ProgressOperationKind: String, Codable, Sendable, Equatable {
    case review
    case setMaximumLeitnerLevel = "set_maximum_leitner_level"
}

/// The reason a question's spaced-repetition state changed.
public enum QuestionReviewOutcome: String, Codable, Sendable, Equatable {
    case correct
    case missed
    case maximumLevelChanged = "maximum_level_changed"
}

/// An immutable, derived record of one question-level progress change.
///
/// Events are deliberately not retained in `ProgressEnvelope`: the ordered
/// operation log and its authoritative pre-operation state are their source.
public struct QuestionReviewEvent: Codable, Sendable, Equatable, Identifiable {
    /// Stable within an operation: `operationID` plus an answer or cap ordinal.
    public let id: String
    public let operationID: String
    public let ordinal: Int
    public let identity: QuestionIdentity
    public let eventTime: Date
    public let outcome: QuestionReviewOutcome
    public let priorLevel: Int
    public let resultingLevel: Int
    public let resultingDueAt: Date
    public let serverRevision: Int?

    public init(
        operationID: String,
        ordinal: Int,
        identity: QuestionIdentity,
        eventTime: Date,
        outcome: QuestionReviewOutcome,
        priorLevel: Int,
        resultingLevel: Int,
        resultingDueAt: Date,
        serverRevision: Int?
    ) {
        precondition(!operationID.isEmpty, "operation IDs must not be empty")
        precondition(ordinal >= 0, "event ordinals must not be negative")
        self.id = "\(operationID):\(ordinal)"
        self.operationID = operationID
        self.ordinal = ordinal
        self.identity = identity
        self.eventTime = eventTime
        self.outcome = outcome
        self.priorLevel = priorLevel
        self.resultingLevel = resultingLevel
        self.resultingDueAt = resultingDueAt
        self.serverRevision = serverRevision
    }
}

public enum ProgressOperationError: Error, Codable, Sendable, Equatable {
    case encodedSizeRefused
    case failed(String)

    private enum CodingKeys: String, CodingKey { case kind, message }
    private enum Kind: String, Codable { case encodedSizeRefused = "encoded_size_refused", failed }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        switch try c.decode(Kind.self, forKey: .kind) {
        case .encodedSizeRefused: self = .encodedSizeRefused
        case .failed: self = .failed(try c.decode(String.self, forKey: .message))
        }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        switch self {
        case .encodedSizeRefused: try c.encode(Kind.encodedSizeRefused, forKey: .kind)
        case .failed(let message):
            try c.encode(Kind.failed, forKey: .kind)
            try c.encode(message, forKey: .message)
        }
    }
}

/// A durable intent. The ID is made once and is retained when an intent is retried.
public struct ProgressOperation: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let createdAt: Date
    public var updatedAt: Date
    public var status: ProgressStatus
    public let kind: ProgressOperationKind
    public let session: SessionDetail?
    public let maximumLeitnerLevel: Int?
    public var error: ProgressOperationError?
    /// Assigned by the authoritative shared progress stream before publish.
    /// Local intents remain nil until the transport reserves a global revision.
    public var serverRevision: Int?

    public var operationID: String { id }

    public var hasValidPayload: Bool {
        switch kind {
        case .review:
            return maximumLeitnerLevel == nil
        case .setMaximumLeitnerLevel:
            return session == nil && maximumLeitnerLevel.map { (1...7).contains($0) } == true
        }
    }

    public init(
        operationID: String = UUID().uuidString.lowercased(),
        createdAt: Date = Date(),
        status: ProgressStatus = .pending,
        kind: ProgressOperationKind = .review,
        session: SessionDetail? = nil,
        maximumLeitnerLevel: Int? = nil,
        error: ProgressOperationError? = nil,
        serverRevision: Int? = nil
    ) {
        precondition(!operationID.isEmpty, "operation IDs must not be empty")
        self.id = operationID
        self.createdAt = createdAt
        self.updatedAt = createdAt
        self.status = status
        self.kind = kind
        self.session = session
        self.maximumLeitnerLevel = maximumLeitnerLevel
        self.error = error
        self.serverRevision = serverRevision
    }

    public static func newIntent(session: SessionDetail, now: Date = Date()) -> ProgressOperation {
        ProgressOperation(createdAt: now, session: session)
    }

    public static func newMaximumLeitnerLevelIntent(_ maximum: Int, now: Date = Date()) -> ProgressOperation {
        ProgressOperation(
            createdAt: now,
            kind: .setMaximumLeitnerLevel,
            maximumLeitnerLevel: maximum
        )
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let id = try container.decode(String.self, forKey: .id)
        guard !id.isEmpty else {
            throw DecodingError.dataCorrupted(.init(
                codingPath: decoder.codingPath + [CodingKeys.id],
                debugDescription: "operation IDs must not be empty"
            ))
        }
        self.id = id
        self.createdAt = try container.decode(Date.self, forKey: .createdAt)
        self.updatedAt = try container.decode(Date.self, forKey: .updatedAt)
        self.status = try container.decode(ProgressStatus.self, forKey: .status)
        self.session = try container.decodeIfPresent(SessionDetail.self, forKey: .session)
        // V1 status-only intents had no session and no kind. They remain
        // review metadata; a maximum-level change always carries its kind.
        self.kind = try container.decodeIfPresent(ProgressOperationKind.self, forKey: .kind) ?? .review
        self.maximumLeitnerLevel = try container.decodeIfPresent(Int.self, forKey: .maximumLeitnerLevel)
        self.error = try container.decodeIfPresent(ProgressOperationError.self, forKey: .error)
        self.serverRevision = try container.decodeIfPresent(Int.self, forKey: .serverRevision)
        guard hasValidPayload else {
            throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath, debugDescription: "invalid progress operation"))
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encode(createdAt, forKey: .createdAt)
        try container.encode(updatedAt, forKey: .updatedAt)
        try container.encode(status, forKey: .status)
        try container.encode(kind, forKey: .kind)
        try container.encodeIfPresent(session, forKey: .session)
        try container.encodeIfPresent(maximumLeitnerLevel, forKey: .maximumLeitnerLevel)
        try container.encodeIfPresent(error, forKey: .error)
        try container.encodeIfPresent(serverRevision, forKey: .serverRevision)
    }

    private enum CodingKeys: String, CodingKey {
        case id, createdAt, updatedAt, status, kind, session, maximumLeitnerLevel, error, serverRevision
    }
}
