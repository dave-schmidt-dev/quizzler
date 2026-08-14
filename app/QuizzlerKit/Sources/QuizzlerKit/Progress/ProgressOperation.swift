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
    public let session: SessionDetail?
    public var error: ProgressOperationError?
    /// Assigned by the authoritative shared progress stream before publish.
    /// Local intents remain nil until the transport reserves a global revision.
    public var serverRevision: Int?

    public var operationID: String { id }

    public init(
        operationID: String = UUID().uuidString.lowercased(),
        createdAt: Date = Date(),
        status: ProgressStatus = .pending,
        session: SessionDetail? = nil,
        error: ProgressOperationError? = nil,
        serverRevision: Int? = nil
    ) {
        precondition(!operationID.isEmpty, "operation IDs must not be empty")
        self.id = operationID
        self.createdAt = createdAt
        self.updatedAt = createdAt
        self.status = status
        self.session = session
        self.error = error
        self.serverRevision = serverRevision
    }

    public static func newIntent(session: SessionDetail, now: Date = Date()) -> ProgressOperation {
        ProgressOperation(createdAt: now, session: session)
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
        self.error = try container.decodeIfPresent(ProgressOperationError.self, forKey: .error)
        self.serverRevision = try container.decodeIfPresent(Int.self, forKey: .serverRevision)
    }

    private enum CodingKeys: String, CodingKey {
        case id, createdAt, updatedAt, status, session, error, serverRevision
    }
}
