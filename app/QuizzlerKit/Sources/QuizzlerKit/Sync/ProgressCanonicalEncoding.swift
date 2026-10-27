import Foundation
import CryptoKit

// MARK: - Canonical evidence

private enum ProgressCanonical {
    static func text(_ value: String) -> String {
        value.precomposedStringWithCanonicalMapping
    }

    static func milliseconds(_ date: Date) -> Int64 {
        Int64((date.timeIntervalSince1970 * 1_000).rounded(.toNearestOrEven))
    }

    static func identity(_ value: QuestionIdentity) -> CanonicalIdentity {
        CanonicalIdentity(
            courseID: text(value.courseID),
            packID: text(value.packID),
            questionID: text(value.questionID)
        )
    }

    static func identityPrecedes(_ left: QuestionIdentity, _ right: QuestionIdentity) -> Bool {
        let leftParts = [left.courseID, left.packID, left.questionID].map { Array(text($0).utf8) }
        let rightParts = [right.courseID, right.packID, right.questionID].map { Array(text($0).utf8) }
        for (l, r) in zip(leftParts, rightParts) {
            if l != r { return l.lexicographicallyPrecedes(r) }
        }
        return false
    }

    static func session(_ value: SessionDetail) -> CanonicalSession {
        CanonicalSession(
            id: text(value.id),
            completedAt: milliseconds(value.completedAt),
            answers: value.answers.map {
                CanonicalAnswer(
                    identity: identity($0.identity),
                    correct: $0.correct,
                    answeredAt: $0.answeredAt.map(milliseconds)
                )
            }
        )
    }

    static func operation(_ value: ProgressOperation) -> CanonicalEnvelopeOperation {
        CanonicalEnvelopeOperation(
            id: text(value.id),
            serverRevision: value.serverRevision,
            createdAt: milliseconds(value.createdAt),
            updatedAt: milliseconds(value.updatedAt),
            status: text(value.status.rawValue),
            kind: value.kind == .review ? nil : text(value.kind.rawValue),
            session: value.session.map(session),
            maximumLeitnerLevel: value.maximumLeitnerLevel,
            error: value.error.map { error in
                switch error {
                case .encodedSizeRefused: return "encoded_size_refused"
                case .failed: return "failed"
                }
            }
        )
    }

    static func srs(_ value: SRSSnapshot) -> CanonicalSRS {
        CanonicalSRS(
            identity: identity(value.identity),
            tier: value.state.tier,
            nextDueAt: milliseconds(value.state.nextDueAt),
            lastReviewedAt: value.state.lastReviewedAt.map(milliseconds),
            intervalDays: value.state.intervalDays,
            reviewCount: value.state.reviewCount
        )
    }

    static func issue(_ value: QuestionIssue) -> CanonicalIssue {
        CanonicalIssue(
            schemaVersion: QuestionIssue.schemaVersion,
            issueID: text(value.issueID),
            courseID: text(value.courseID),
            packID: text(value.packID),
            questionID: text(value.questionID),
            questionType: text(value.questionType.rawValue),
            appVersion: text(value.appVersion),
            build: text(value.build),
            selectedResponse: value.selectedResponse.map(text),
            description: text(value.description)
        )
    }
}

private struct CanonicalIdentity: Encodable {
    let courseID: String
    let packID: String
    let questionID: String
}

private struct CanonicalAnswer: Encodable {
    let identity: CanonicalIdentity
    let correct: Bool
    let answeredAt: Int64?
}

private struct CanonicalSession: Encodable {
    let id: String
    let completedAt: Int64
    let answers: [CanonicalAnswer]
}

private struct CanonicalEnvelopeOperation: Encodable {
    let id: String
    let serverRevision: Int?
    let createdAt: Int64
    let updatedAt: Int64
    let status: String
    let kind: String?
    let session: CanonicalSession?
    let maximumLeitnerLevel: Int?
    let error: String?

    enum CodingKeys: String, CodingKey { case id, serverRevision, createdAt, updatedAt, status, kind, session, maximumLeitnerLevel, error }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encodeIfPresent(serverRevision, forKey: .serverRevision)
        try c.encode(createdAt, forKey: .createdAt)
        try c.encode(updatedAt, forKey: .updatedAt)
        try c.encode(status, forKey: .status)
        try c.encodeIfPresent(kind, forKey: .kind)
        try c.encodeIfPresent(session, forKey: .session)
        try c.encodeIfPresent(maximumLeitnerLevel, forKey: .maximumLeitnerLevel)
        try c.encodeIfPresent(error, forKey: .error)
    }
}

private struct CanonicalMastery: Encodable {
    let identity: CanonicalIdentity
    let answered: Int
    let correct: Int
}

private struct CanonicalSRS: Encodable {
    let identity: CanonicalIdentity
    let tier: Int
    let nextDueAt: Int64
    let lastReviewedAt: Int64?
    let intervalDays: Int
    let reviewCount: Int

    enum CodingKeys: String, CodingKey { case identity, tier, nextDueAt, lastReviewedAt, intervalDays, reviewCount }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(identity, forKey: .identity)
        try c.encode(tier, forKey: .tier)
        try c.encode(nextDueAt, forKey: .nextDueAt)
        try c.encodeIfPresent(lastReviewedAt, forKey: .lastReviewedAt)
        try c.encode(intervalDays, forKey: .intervalDays)
        try c.encode(reviewCount, forKey: .reviewCount)
    }
}

private struct CanonicalIssue: Encodable {
    let schemaVersion: Int
    let issueID: String
    let courseID: String
    let packID: String
    let questionID: String
    let questionType: String
    let appVersion: String
    let build: String
    let selectedResponse: String?
    let description: String

    enum CodingKeys: String, CodingKey {
        case schemaVersion, issueID, courseID, packID, questionID, questionType
        case appVersion, build, selectedResponse, description
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(schemaVersion, forKey: .schemaVersion)
        try c.encode(issueID, forKey: .issueID)
        try c.encode(courseID, forKey: .courseID)
        try c.encode(packID, forKey: .packID)
        try c.encode(questionID, forKey: .questionID)
        try c.encode(questionType, forKey: .questionType)
        try c.encode(appVersion, forKey: .appVersion)
        try c.encode(build, forKey: .build)
        try c.encodeIfPresent(selectedResponse, forKey: .selectedResponse)
        try c.encode(description, forKey: .description)
    }
}

private struct CanonicalMergeOperation: Encodable {
    let operationID: String
    let baseRevision: Int
    let baseOperationID: String?
    let serverRevision: Int
    let createdAt: Int64
    let updatedAt: Int64
    let kind: String?
    let session: CanonicalSession?
    let maximumLeitnerLevel: Int?
}

private struct CanonicalTombstone: Encodable {
    let operationID: String
    let serverRevision: Int
}

private struct CanonicalEnvelope: Encodable {
    let protocolName: String
    let schemaVersion: Int
    let documentRevision: Int
    let actorID: String
    let operationID: String
    let createdAt: Int64
    let sessionDetails: [CanonicalSession]
    let sessionsTotal: Int
    let answered: Int
    let correct: Int
    let mastery: [CanonicalMastery]
    let srs: [CanonicalSRS]
    let maximumLeitnerLevel: Int?
    let compactionVersion: Int
    let compactionWatermarkRevision: Int
    let operations: [CanonicalEnvelopeOperation]
    let issues: [CanonicalIssue]
}

private struct CanonicalSnapshot: Encodable {
    let envelope: CanonicalEnvelope
    let operations: [CanonicalMergeOperation]
    let tombstones: [CanonicalTombstone]
}

public extension ProgressMergeSnapshot {
    /// Canonical semantic bytes normalize strings to NFC, timestamps to
    /// integer milliseconds, and all set-like collections to UTF-8 order.
    func canonicalSemanticData() throws -> Data {
        let sortedMastery = envelope.mastery.sorted {
            ProgressCanonical.identityPrecedes($0.identity, $1.identity)
        }.map { CanonicalMastery(identity: ProgressCanonical.identity($0.identity), answered: $0.answered, correct: $0.correct) }
        let sortedSRS = envelope.srs.sorted {
            ProgressCanonical.identityPrecedes($0.identity, $1.identity)
        }.map(ProgressCanonical.srs)
        let sortedIssues = envelope.issues.sorted {
            Array(ProgressCanonical.text($0.issueID).utf8).lexicographicallyPrecedes(Array(ProgressCanonical.text($1.issueID).utf8))
        }.map(ProgressCanonical.issue)
        let sortedEnvelopeOperations = envelope.operations.sorted {
            Array(ProgressCanonical.text($0.id).utf8).lexicographicallyPrecedes(Array(ProgressCanonical.text($1.id).utf8))
        }.map(ProgressCanonical.operation)
        let sortedTombstones = tombstones.sorted {
            if $0.serverRevision != $1.serverRevision { return $0.serverRevision < $1.serverRevision }
            return Array(ProgressCanonical.text($0.operationID).utf8).lexicographicallyPrecedes(Array(ProgressCanonical.text($1.operationID).utf8))
        }.map { CanonicalTombstone(operationID: ProgressCanonical.text($0.operationID), serverRevision: $0.serverRevision) }
        let canonical = CanonicalSnapshot(
            envelope: CanonicalEnvelope(
                protocolName: ProgressCanonical.text(envelope.protocolName),
                schemaVersion: envelope.schemaVersion,
                documentRevision: envelope.documentRevision,
                actorID: ProgressCanonical.text(envelope.actorID),
                operationID: ProgressCanonical.text(envelope.operationID),
                createdAt: ProgressCanonical.milliseconds(envelope.createdAt),
                sessionDetails: envelope.sessionDetails.map(ProgressCanonical.session),
                sessionsTotal: envelope.aggregate.sessionsTotal,
                answered: envelope.aggregate.answered,
                correct: envelope.aggregate.correct,
                mastery: sortedMastery,
                srs: sortedSRS,
                maximumLeitnerLevel: envelope.maximumLeitnerLevel == 5 ? nil : envelope.maximumLeitnerLevel,
                compactionVersion: envelope.compaction.version,
                compactionWatermarkRevision: envelope.compaction.watermarkRevision,
                operations: sortedEnvelopeOperations,
                issues: sortedIssues
            ),
            operations: ProgressMergeEngine.ordered(operations).map {
                CanonicalMergeOperation(
                    operationID: ProgressCanonical.text($0.operationID),
                    baseRevision: $0.baseRevision,
                    baseOperationID: $0.baseOperationID.map(ProgressCanonical.text),
                    serverRevision: $0.serverRevision,
                    createdAt: ProgressCanonical.milliseconds($0.createdAt),
                    updatedAt: ProgressCanonical.milliseconds($0.updatedAt),
                    kind: $0.kind == .review ? nil : ProgressCanonical.text($0.kind.rawValue),
                    session: $0.session.map(ProgressCanonical.session),
                    maximumLeitnerLevel: $0.maximumLeitnerLevel
                )
            },
            tombstones: sortedTombstones
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        return try encoder.encode(canonical)
    }

    func canonicalSemanticHash() throws -> String {
        SHA256.hash(data: try canonicalSemanticData())
            .map { String(format: "%02x", $0) }
            .joined()
    }

    func canonicalEvidenceHash() throws -> String { try canonicalSemanticHash() }
}
