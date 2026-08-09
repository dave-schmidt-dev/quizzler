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

    public var operationID: String { id }

    public init(
        operationID: String = UUID().uuidString.lowercased(),
        createdAt: Date = Date(),
        status: ProgressStatus = .pending,
        session: SessionDetail? = nil,
        error: ProgressOperationError? = nil
    ) {
        precondition(!operationID.isEmpty, "operation IDs must not be empty")
        self.id = operationID
        self.createdAt = createdAt
        self.updatedAt = createdAt
        self.status = status
        self.session = session
        self.error = error
    }

    public static func newIntent(session: SessionDetail, now: Date = Date()) -> ProgressOperation {
        ProgressOperation(createdAt: now, session: session)
    }
}
