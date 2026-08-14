import Foundation

public enum ProgressRepositoryError: Error, Sendable, Equatable {
    case corruptState
    case encodedSizeRefused
    case operationNotFound
    case invalidOperation
    case failed(String)
}

/// A completed answer deliberately retains its full course/pack/question tuple.
public struct SessionAnswer: Codable, Sendable, Equatable {
    public let identity: QuestionIdentity
    public let correct: Bool

    public init(identity: QuestionIdentity, correct: Bool) {
        self.identity = identity
        self.correct = correct
    }

    public init(courseID: String, packID: String, questionID: String, correct: Bool) {
        self.init(identity: QuestionIdentity(courseID: courseID, packID: packID, questionID: questionID), correct: correct)
    }

    enum CodingKeys: String, CodingKey {
        case courseID = "course_id", packID = "pack_id", questionID = "question_id", correct
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            courseID: try c.decode(String.self, forKey: .courseID),
            packID: try c.decode(String.self, forKey: .packID),
            questionID: try c.decode(String.self, forKey: .questionID),
            correct: try c.decode(Bool.self, forKey: .correct)
        )
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(identity.courseID, forKey: .courseID)
        try c.encode(identity.packID, forKey: .packID)
        try c.encode(identity.questionID, forKey: .questionID)
        try c.encode(correct, forKey: .correct)
    }
}

public struct SessionDetail: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let completedAt: Date
    public let answers: [SessionAnswer]

    public var sessionID: String { id }

    public init(sessionID: String = UUID().uuidString.lowercased(), completedAt: Date = Date(), answers: [SessionAnswer]) {
        precondition(!sessionID.isEmpty, "session IDs must not be empty")
        self.id = sessionID
        self.completedAt = completedAt
        self.answers = answers
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let id = try container.decode(String.self, forKey: .id)
        guard !id.isEmpty else {
            throw DecodingError.dataCorrupted(.init(
                codingPath: decoder.codingPath + [CodingKeys.id],
                debugDescription: "session IDs must not be empty"
            ))
        }
        self.id = id
        self.completedAt = try container.decode(Date.self, forKey: .completedAt)
        self.answers = try container.decode([SessionAnswer].self, forKey: .answers)
    }

    private enum CodingKeys: String, CodingKey { case id, completedAt, answers }
}

public struct AggregateSnapshot: Codable, Sendable, Equatable {
    public var sessionsTotal: Int
    public var answered: Int
    public var correct: Int

    public init(sessionsTotal: Int = 0, answered: Int = 0, correct: Int = 0) {
        self.sessionsTotal = sessionsTotal
        self.answered = answered
        self.correct = correct
    }
}

public struct MasterySnapshot: Codable, Sendable, Equatable {
    public let identity: QuestionIdentity
    public var answered: Int
    public var correct: Int

    public init(identity: QuestionIdentity, answered: Int = 0, correct: Int = 0) {
        self.identity = identity
        self.answered = answered
        self.correct = correct
    }
}

public struct SRSSnapshot: Codable, Sendable, Equatable {
    public let identity: QuestionIdentity
    public let state: SRSState

    public init(identity: QuestionIdentity, state: SRSState) {
        self.identity = identity
        self.state = state
    }
}

public struct ProgressCompaction: Codable, Sendable, Equatable {
    public let version: Int
    public var watermarkRevision: Int

    public init(version: Int = 1, watermarkRevision: Int = 0) {
        self.version = version
        self.watermarkRevision = watermarkRevision
    }
}

/// The complete local cache. It contains no question text, pack manifests, or assets.
public struct ProgressEnvelope: Codable, Sendable, Equatable {
    public let protocolName: String
    public let schemaVersion: Int
    public var documentRevision: Int
    public let actorID: String
    public var operationID: String
    public let createdAt: Date
    public var sessionDetails: [SessionDetail]
    public var aggregate: AggregateSnapshot
    public var mastery: [MasterySnapshot]
    public var srs: [SRSSnapshot]
    public var compaction: ProgressCompaction
    public var operations: [ProgressOperation]
    public var issues: [QuestionIssue]

    public init(
        schemaVersion: Int = 1,
        documentRevision: Int = 0,
        actorID: String,
        operationID: String = UUID().uuidString.lowercased(),
        createdAt: Date = Date(),
        sessionDetails: [SessionDetail] = [],
        aggregate: AggregateSnapshot = .init(),
        mastery: [MasterySnapshot] = [],
        srs: [SRSSnapshot] = [],
        compaction: ProgressCompaction = .init(),
        operations: [ProgressOperation] = [],
        issues: [QuestionIssue] = []
    ) {
        self.protocolName = "quizzler-progress"
        self.schemaVersion = schemaVersion
        self.documentRevision = documentRevision
        self.actorID = actorID
        self.operationID = operationID
        self.createdAt = createdAt
        self.sessionDetails = sessionDetails
        self.aggregate = aggregate
        self.mastery = mastery
        self.srs = srs
        self.compaction = compaction
        self.operations = operations
        self.issues = issues
    }

    public static let sessionRetention = 200
    public static let operationRetention = 4_096
    public static let operationRetentionDays = 30
    private static let protocolIdentifier = "quizzler-progress"

    // This is the published SRS ladder in REPORT_SCHEMA.md.  Keeping the
    // ladder here makes an answer replay deterministic and avoids deriving a
    // schedule from wall-clock state already present in a snapshot.
    private static let srsIntervalsDays = [1, 3, 7, 14, 30, 60, 120]

    private static func srsState(
        current: SRSState?,
        correct: Bool,
        reviewedAt: Date
    ) -> SRSState {
        let currentTier = current?.tier ?? 1
        let rating: SRSRating = correct ? .good : .again
        let tier: Int
        switch rating {
        case .again:
            tier = max(1, currentTier - 2)
        case .good:
            tier = min(7, currentTier + 1)
        case .hard, .easy:
            // SessionAnswer only carries correctness. These ratings are
            // deliberately unreachable until the answer contract carries a
            // user-selected rating.
            tier = currentTier
        }
        let intervalDays = srsIntervalsDays[tier - 1]
        return try! SRSState(
            tier: tier,
            nextDueAt: reviewedAt.addingTimeInterval(Double(intervalDays) * 86_400),
            lastReviewedAt: reviewedAt,
            intervalDays: intervalDays,
            reviewCount: (current?.reviewCount ?? 0) + 1
        )
    }

    mutating func applying(_ session: SessionDetail, operation: ProgressOperation) {
        aggregate.sessionsTotal += 1
        aggregate.answered += session.answers.count
        aggregate.correct += session.answers.filter(\.correct).count
        for answer in session.answers {
            if let index = mastery.firstIndex(where: { $0.identity == answer.identity }) {
                mastery[index].answered += 1
                mastery[index].correct += answer.correct ? 1 : 0
            } else {
                mastery.append(MasterySnapshot(identity: answer.identity, answered: 1, correct: answer.correct ? 1 : 0))
            }

            let current = srs.first(where: { $0.identity == answer.identity })?.state
            let next = SRSSnapshot(
                identity: answer.identity,
                state: Self.srsState(current: current, correct: answer.correct, reviewedAt: operation.updatedAt)
            )
            if let index = srs.firstIndex(where: { $0.identity == answer.identity }) {
                srs[index] = next
            } else {
                srs.append(next)
            }
        }
        sessionDetails.append(session)
        if sessionDetails.count > Self.sessionRetention {
            sessionDetails.removeFirst(sessionDetails.count - Self.sessionRetention)
        }
        operations.append(operation)
        // Operation retention is independent of the session-detail window.
        // Never evict pending or failed work that still needs a retry.
        let cutoff = operation.updatedAt.addingTimeInterval(-Double(Self.operationRetentionDays) * 86_400)
        operations.removeAll { candidate in
            candidate.status == .applied && candidate.id != operation.id && candidate.updatedAt < cutoff
        }
        let applied = operations.filter { $0.status == .applied }
        if applied.count > Self.operationRetention {
            let remove = applied.sorted { $0.updatedAt < $1.updatedAt }
                .prefix(applied.count - Self.operationRetention)
            let removeIDs = Set(remove.map(\.id))
            operations.removeAll { removeIDs.contains($0.id) }
        }
        documentRevision += 1
        operationID = operation.id
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let protocolName = try container.decode(String.self, forKey: .protocolName)
        let schemaVersion = try container.decode(Int.self, forKey: .schemaVersion)
        let documentRevision = try container.decode(Int.self, forKey: .documentRevision)
        let actorID = try container.decode(String.self, forKey: .actorID)
        let operationID = try container.decode(String.self, forKey: .operationID)
        let createdAt = try container.decode(Date.self, forKey: .createdAt)
        let sessionDetails = try container.decode([SessionDetail].self, forKey: .sessionDetails)
        let aggregate = try container.decode(AggregateSnapshot.self, forKey: .aggregate)
        let mastery = try container.decode([MasterySnapshot].self, forKey: .mastery)
        let srs = try container.decode([SRSSnapshot].self, forKey: .srs)
        let compaction = try container.decode(ProgressCompaction.self, forKey: .compaction)
        let operations = try container.decode([ProgressOperation].self, forKey: .operations)
        let issues = try container.decode([QuestionIssue].self, forKey: .issues)

        guard protocolName == Self.protocolIdentifier,
              schemaVersion == 1,
              documentRevision >= 0,
              !actorID.isEmpty,
              !operationID.isEmpty,
              sessionDetails.count <= Self.sessionRetention,
              aggregate.sessionsTotal >= 0,
              aggregate.answered >= 0,
              aggregate.correct >= 0,
              aggregate.correct <= aggregate.answered,
              compaction.version == 1,
              compaction.watermarkRevision >= 0,
              compaction.watermarkRevision <= documentRevision,
              operations.count <= Self.operationRetention,
              Set(mastery.map(\.identity)).count == mastery.count,
              Set(srs.map(\.identity)).count == srs.count,
              Set(operations.map(\.id)).count == operations.count,
              mastery.allSatisfy({ $0.answered >= 0 && $0.correct >= 0 && $0.correct <= $0.answered }),
              aggregate.sessionsTotal >= sessionDetails.count
        else {
            throw DecodingError.dataCorrupted(.init(
                codingPath: decoder.codingPath,
                debugDescription: "invalid progress envelope"
            ))
        }

        self.protocolName = protocolName
        self.schemaVersion = schemaVersion
        self.documentRevision = documentRevision
        self.actorID = actorID
        self.operationID = operationID
        self.createdAt = createdAt
        self.sessionDetails = sessionDetails
        self.aggregate = aggregate
        self.mastery = mastery
        self.srs = srs
        self.compaction = compaction
        self.operations = operations
        self.issues = issues
    }

    private enum CodingKeys: String, CodingKey {
        case protocolName, schemaVersion, documentRevision, actorID, operationID
        case createdAt, sessionDetails, aggregate, mastery, srs, compaction, operations, issues
    }
}

public actor ProgressRepository {
    private let store: LocalProgressStore
    private let actorID: String

    public init(actorID: String, store: LocalProgressStore = LocalProgressStore()) {
        self.actorID = actorID
        self.store = store
    }

    public func snapshot() async throws -> ProgressEnvelope {
        do { return try await store.read() ?? ProgressEnvelope(actorID: actorID) }
        catch LocalProgressStoreError.corruptState { throw ProgressRepositoryError.corruptState }
        catch { throw ProgressRepositoryError.failed("read_failed") }
    }

    /// Saves one intent atomically. A failed write leaves the previous envelope untouched.
    @discardableResult
    public func save(_ session: SessionDetail, operationID: String? = nil, now: Date = Date()) async throws -> ProgressOperation {
        let suppliedOperationID = operationID != nil
        let id = operationID ?? UUID().uuidString.lowercased()
        let envelope = try await update { envelope in
            if let existing = envelope.operations.first(where: { $0.id == id }) {
                guard existing.session == session else { throw ProgressRepositoryError.invalidOperation }
                if existing.status == .applied { return false }
            }
            // The bounded operation log is the durable idempotency ledger.
            // Once an applied ID has been compacted, accepting it as a new
            // explicit intent would double-count a delayed replay. Callers
            // can create new work by omitting `operationID`; a supplied ID
            // must have been durably enqueued or retained for retry.
            guard !suppliedOperationID else {
                throw ProgressRepositoryError.operationNotFound
            }
            var operation = envelope.operations.first(where: { $0.id == id })
                ?? ProgressOperation(operationID: id, createdAt: now, status: .applied, session: session)
            operation.status = .applied
            operation.error = nil
            operation.updatedAt = now
            envelope.operations.removeAll { $0.id == id }
            envelope.applying(session, operation: operation)
            return true
        }
        guard let operation = envelope.operations.first(where: { $0.id == id }) else {
            throw ProgressRepositoryError.failed("save_missing_operation")
        }
        return operation
    }

    /// Persists an intent before a remote sync attempt. Retrying it reuses its ID.
    @discardableResult
    public func enqueue(_ operation: ProgressOperation) async throws -> ProgressOperation {
        guard operation.status == .pending else { throw ProgressRepositoryError.invalidOperation }
        let envelope = try await update { envelope in
            if let existing = envelope.operations.first(where: { $0.id == operation.id }) {
                guard existing.session == operation.session else { throw ProgressRepositoryError.invalidOperation }
                return false
            }
            envelope.operations.append(operation)
            return true
        }
        return envelope.operations.first(where: { $0.id == operation.id }) ?? operation
    }

    /// Returns the same persisted intent for another attempt; a retry never
    /// allocates a second operation ID.
    public func retry(operationID: String) async throws -> ProgressOperation {
        let envelope = try await update { envelope in
            guard let index = envelope.operations.firstIndex(where: { $0.id == operationID }) else {
                throw ProgressRepositoryError.operationNotFound
            }
            guard envelope.operations[index].status == .pending || envelope.operations[index].status == .failed else {
                throw ProgressRepositoryError.invalidOperation
            }
            envelope.operations[index].status = .pending
            envelope.operations[index].error = nil
            envelope.operations[index].updatedAt = Date()
            return true
        }
        guard let result = envelope.operations.first(where: { $0.id == operationID }) else {
            throw ProgressRepositoryError.failed("retry_missing_operation")
        }
        return result
    }

    /// Permanently records a failed attempt for a pending intent. The intent
    /// remains retryable and is not allowed to affect any progress snapshot.
    @discardableResult
    public func markFailed(operationID: String, error: ProgressOperationError) async throws -> ProgressOperation {
        let envelope = try await update { envelope in
            guard let index = envelope.operations.firstIndex(where: { $0.id == operationID }) else {
                throw ProgressRepositoryError.operationNotFound
            }
            guard envelope.operations[index].status == .pending else {
                throw ProgressRepositoryError.invalidOperation
            }
            envelope.operations[index].status = .failed
            envelope.operations[index].error = error
            envelope.operations[index].updatedAt = Date()
            return true
        }
        guard let result = envelope.operations.first(where: { $0.id == operationID }) else {
            throw ProgressRepositoryError.failed("mark_failed_missing_operation")
        }
        return result
    }

    @discardableResult
    public func queueIssue(_ issue: QuestionIssue) async throws -> QuestionIssue {
        let envelope = try await update { envelope in
            if let existing = envelope.issues.first(where: { $0.issueID == issue.issueID }) {
                guard existing == issue else { throw ProgressRepositoryError.invalidOperation }
                return false
            }
            envelope.issues.append(issue)
            return true
        }
        return envelope.issues.first(where: { $0.issueID == issue.issueID }) ?? issue
    }

    private func update(
        _ mutation: @Sendable (inout ProgressEnvelope) throws -> Bool
    ) async throws -> ProgressEnvelope {
        do {
            return try await store.modify { [actorID] existing in
                var envelope = existing ?? ProgressEnvelope(actorID: actorID)
                guard try mutation(&envelope) else { return nil }
                return envelope
            } ?? ProgressEnvelope(actorID: actorID)
        } catch let error as ProgressRepositoryError {
            throw error
        } catch LocalProgressStoreError.encodedSizeRefused {
            throw ProgressRepositoryError.encodedSizeRefused
        } catch LocalProgressStoreError.corruptState {
            throw ProgressRepositoryError.corruptState
        } catch {
            throw ProgressRepositoryError.failed("mutation_failed")
        }
    }
}
