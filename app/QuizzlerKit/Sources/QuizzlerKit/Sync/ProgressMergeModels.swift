import Foundation

/// An operation as it appears in the shared progress log.  The server
/// revision is the only ordering input supplied by a client; timestamps are
/// retained for audit and retention, but never decide which operation wins.
public struct ProgressMergeOperation: Codable, Sendable, Equatable, Identifiable {
    public let operationID: String
    public let baseRevision: Int
    public let baseOperationID: String?
    public let serverRevision: Int
    public let createdAt: Date
    public let updatedAt: Date
    /// Trusted ingest time used for retention; client timestamps are advisory.
    public let serverRecordedAt: Date
    public let kind: ProgressOperationKind
    public let session: SessionDetail?
    public let maximumLeitnerLevel: Int?

    public var id: String { operationID }

    public init(
        operationID: String,
        baseRevision: Int,
        baseOperationID: String? = nil,
        serverRevision: Int,
        createdAt: Date,
        updatedAt: Date? = nil,
        serverRecordedAt: Date? = nil,
        session: SessionDetail?
    ) {
        precondition(!operationID.isEmpty, "operation IDs must not be empty")
        precondition(baseRevision >= 0, "base revisions must not be negative")
        precondition(serverRevision > 0, "server revisions start at one")
        precondition(session != nil, "review operations require a session")
        self.operationID = operationID
        self.baseRevision = baseRevision
        self.baseOperationID = baseOperationID
        self.serverRevision = serverRevision
        self.createdAt = createdAt
        self.updatedAt = updatedAt ?? createdAt
        self.serverRecordedAt = serverRecordedAt ?? Date()
        self.kind = .review
        self.session = session
        self.maximumLeitnerLevel = nil
    }

    public init(
        operationID: String,
        baseRevision: Int,
        baseOperationID: String? = nil,
        serverRevision: Int,
        createdAt: Date,
        updatedAt: Date? = nil,
        serverRecordedAt: Date? = nil,
        maximumLeitnerLevel: Int
    ) {
        precondition(!operationID.isEmpty, "operation IDs must not be empty")
        precondition(baseRevision >= 0, "base revisions must not be negative")
        precondition(serverRevision > 0, "server revisions start at one")
        precondition((1...7).contains(maximumLeitnerLevel), "maximum Leitner level must be 1...7")
        self.operationID = operationID
        self.baseRevision = baseRevision
        self.baseOperationID = baseOperationID
        self.serverRevision = serverRevision
        self.createdAt = createdAt
        self.updatedAt = updatedAt ?? createdAt
        self.serverRecordedAt = serverRecordedAt ?? Date()
        self.kind = .setMaximumLeitnerLevel
        self.session = nil
        self.maximumLeitnerLevel = maximumLeitnerLevel
    }

    public init(
        operation: ProgressOperation,
        baseRevision: Int,
        serverRevision: Int,
        serverRecordedAt: Date? = nil
    ) throws {
        guard !operation.id.isEmpty, baseRevision >= 0, serverRevision > 0 else {
            throw ProgressMergeError.invalidOperation
        }
        switch operation.kind {
        case .review:
            guard let session = operation.session else { throw ProgressMergeError.missingSession }
            self.init(
                operationID: operation.id,
                baseRevision: baseRevision,
                serverRevision: serverRevision,
                createdAt: operation.createdAt,
                updatedAt: operation.updatedAt,
                serverRecordedAt: serverRecordedAt,
                session: session
            )
        case .setMaximumLeitnerLevel:
            guard let maximum = operation.maximumLeitnerLevel else { throw ProgressMergeError.invalidOperation }
            self.init(
                operationID: operation.id,
                baseRevision: baseRevision,
                serverRevision: serverRevision,
                createdAt: operation.createdAt,
                updatedAt: operation.updatedAt,
                serverRecordedAt: serverRecordedAt,
                maximumLeitnerLevel: maximum
            )
        }
    }

    private enum CodingKeys: String, CodingKey {
        case operationID, baseRevision, baseOperationID, serverRevision, createdAt, updatedAt, serverRecordedAt, kind, session, maximumLeitnerLevel
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let operationID = try c.decode(String.self, forKey: .operationID)
        let baseRevision = try c.decode(Int.self, forKey: .baseRevision)
        let serverRevision = try c.decode(Int.self, forKey: .serverRevision)
        guard !operationID.isEmpty, baseRevision >= 0, serverRevision > 0 else {
            throw ProgressMergeError.invalidOperation
        }
        let updatedAt = try c.decode(Date.self, forKey: .updatedAt)
        let kind = try c.decodeIfPresent(ProgressOperationKind.self, forKey: .kind) ?? .review
        switch kind {
        case .review:
            self.init(
                operationID: operationID,
                baseRevision: baseRevision,
                baseOperationID: try c.decodeIfPresent(String.self, forKey: .baseOperationID),
                serverRevision: serverRevision,
                createdAt: try c.decode(Date.self, forKey: .createdAt),
                updatedAt: updatedAt,
                serverRecordedAt: try c.decodeIfPresent(Date.self, forKey: .serverRecordedAt) ?? updatedAt,
                session: try c.decode(SessionDetail.self, forKey: .session)
            )
        case .setMaximumLeitnerLevel:
            self.init(
                operationID: operationID,
                baseRevision: baseRevision,
                baseOperationID: try c.decodeIfPresent(String.self, forKey: .baseOperationID),
                serverRevision: serverRevision,
                createdAt: try c.decode(Date.self, forKey: .createdAt),
                updatedAt: updatedAt,
                serverRecordedAt: try c.decodeIfPresent(Date.self, forKey: .serverRecordedAt) ?? updatedAt,
                maximumLeitnerLevel: try c.decode(Int.self, forKey: .maximumLeitnerLevel)
            )
        }
    }

    /// The payload fields which an idempotent replay must preserve.
    public var semanticPayload: ProgressMergePayload {
        ProgressMergePayload(
            operationID: operationID,
            baseRevision: baseRevision,
            baseOperationID: baseOperationID,
            serverRevision: serverRevision,
            createdAt: createdAt,
            updatedAt: updatedAt,
            kind: kind,
            session: session,
            maximumLeitnerLevel: maximumLeitnerLevel
        )
    }
}

public struct ProgressMergePayload: Codable, Sendable, Equatable {
    public let operationID: String
    public let baseRevision: Int
    public let baseOperationID: String?
    public let serverRevision: Int
    public let createdAt: Date
    public let updatedAt: Date
    public let kind: ProgressOperationKind
    public let session: SessionDetail?
    public let maximumLeitnerLevel: Int?

    public init(
        operationID: String,
        baseRevision: Int,
        baseOperationID: String?,
        serverRevision: Int,
        createdAt: Date,
        updatedAt: Date,
        kind: ProgressOperationKind,
        session: SessionDetail?,
        maximumLeitnerLevel: Int?
    ) {
        self.operationID = operationID
        self.baseRevision = baseRevision
        self.baseOperationID = baseOperationID
        self.serverRevision = serverRevision
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.kind = kind
        self.session = session
        self.maximumLeitnerLevel = maximumLeitnerLevel
    }

    public init(
        operationID: String,
        baseRevision: Int = 0,
        baseOperationID: String? = nil,
        serverRevision: Int = 0,
        createdAt: Date = Date(timeIntervalSince1970: 0),
        updatedAt: Date = Date(timeIntervalSince1970: 0),
        session: SessionDetail?
    ) {
        self.operationID = operationID
        self.baseRevision = baseRevision
        self.baseOperationID = baseOperationID
        self.serverRevision = serverRevision
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.kind = .review
        self.session = session
        self.maximumLeitnerLevel = nil
    }

    public init(
        operationID: String,
        baseRevision: Int = 0,
        baseOperationID: String? = nil,
        serverRevision: Int = 0,
        createdAt: Date = Date(timeIntervalSince1970: 0),
        updatedAt: Date = Date(timeIntervalSince1970: 0),
        maximumLeitnerLevel: Int
    ) {
        self.operationID = operationID
        self.baseRevision = baseRevision
        self.baseOperationID = baseOperationID
        self.serverRevision = serverRevision
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.kind = .setMaximumLeitnerLevel
        self.session = nil
        self.maximumLeitnerLevel = maximumLeitnerLevel
    }
}

public struct ProgressMergeTombstone: Codable, Sendable, Equatable, Hashable {
    public let operationID: String
    public let serverRevision: Int

    public init(operationID: String, serverRevision: Int) {
        self.operationID = operationID
        self.serverRevision = serverRevision
    }
}

public struct ProgressMergeSnapshot: Codable, Sendable, Equatable {
    public var envelope: ProgressEnvelope
    public var operations: [ProgressMergeOperation]
    public var tombstones: [ProgressMergeTombstone]

    public init(
        envelope: ProgressEnvelope,
        operations: [ProgressMergeOperation] = [],
        tombstones: [ProgressMergeTombstone] = []
    ) throws {
        let operationIDs = Set(operations.map(\.operationID))
        let tombstoneIDs = Set(tombstones.map(\.operationID))
        guard Set(operations.map(\.operationID)).count == operations.count,
              Set(tombstones.map(\.operationID)).count == tombstones.count,
              Set(envelope.operations.map(\.id)).count == envelope.operations.count,
              operationIDs.isDisjoint(with: tombstoneIDs),
              Set(envelope.operations.compactMap { $0.serverRevision == nil ? nil : $0.id })
                  .isSubset(of: operationIDs),
              operations.allSatisfy({ $0.serverRevision > envelope.compaction.watermarkRevision }),
              tombstones.allSatisfy({ $0.serverRevision > 0 })
        else { throw ProgressMergeError.invalidSnapshot }
        let operationsByID = Dictionary(uniqueKeysWithValues: operations.map { ($0.operationID, $0) })
        guard envelope.operations.allSatisfy({ envelopeOperation in
            guard let mergeOperation = operationsByID[envelopeOperation.id] else { return true }
            return mergeOperation.kind == envelopeOperation.kind
                && mergeOperation.session == envelopeOperation.session
                && mergeOperation.maximumLeitnerLevel == envelopeOperation.maximumLeitnerLevel
                && mergeOperation.createdAt == envelopeOperation.createdAt
                && (envelopeOperation.serverRevision == nil
                    || envelopeOperation.serverRevision == mergeOperation.serverRevision)
        }) else { throw ProgressMergeError.invalidSnapshot }
        self.envelope = envelope
        self.operations = operations
        self.tombstones = tombstones
    }

    public var watermarkRevision: Int { envelope.compaction.watermarkRevision }

    public static func empty(
        actorID: String,
        createdAt: Date = Date(timeIntervalSince1970: 0),
        operationID: String = "baseline"
    ) -> ProgressMergeSnapshot {
        try! ProgressMergeSnapshot(envelope: ProgressEnvelope(
            actorID: actorID,
            operationID: operationID,
            createdAt: createdAt
        ))
    }

    private enum CodingKeys: String, CodingKey { case envelope, operations, tombstones }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let envelope = try c.decode(ProgressEnvelope.self, forKey: .envelope)
        let operations = try c.decode([ProgressMergeOperation].self, forKey: .operations)
        let tombstones = try c.decode([ProgressMergeTombstone].self, forKey: .tombstones)
        try self.init(
            envelope: envelope,
            operations: operations,
            tombstones: tombstones
        )
    }
}

public enum ProgressMergeError: Error, Codable, Sendable, Equatable {
    case invalidOperation
    case invalidSnapshot
    case invalidRevision
    case missingSession
    case duplicateOperationPayloadMismatch
    case revisionConflict(currentRevision: Int, operationBaseRevision: Int)
    case orderingConflict(currentRevision: Int, currentOperationID: String)
    case manualResolutionRequired
    case pendingOperationWouldBePruned
    case tombstoneRetentionExceeded
}

public struct ProgressMergeResult: Sendable, Equatable {
    public let snapshot: ProgressMergeSnapshot
    public let appliedOperationIDs: [String]
    public let duplicateOperationIDs: [String]
    public let rebased: Bool
    /// Derived only while applying newly accepted operations.
    public let reviewEvents: [QuestionReviewEvent]

    public init(
        snapshot: ProgressMergeSnapshot,
        appliedOperationIDs: [String],
        duplicateOperationIDs: [String],
        rebased: Bool,
        reviewEvents: [QuestionReviewEvent] = []
    ) {
        self.snapshot = snapshot
        self.appliedOperationIDs = appliedOperationIDs
        self.duplicateOperationIDs = duplicateOperationIDs
        self.rebased = rebased
        self.reviewEvents = reviewEvents
    }
}

public struct ProgressMergeDivergence: Sendable, Equatable {
    public let local: ProgressMergeSnapshot
    public let remote: ProgressMergeSnapshot

    public init(local: ProgressMergeSnapshot, remote: ProgressMergeSnapshot) {
        self.local = local
        self.remote = remote
    }
}
