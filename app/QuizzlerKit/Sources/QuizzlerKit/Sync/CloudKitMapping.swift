import Foundation
import CryptoKit

#if canImport(CloudKit)
import CloudKit
#endif

/// The only record types used by native progress sync. Packs and question text
/// deliberately have no CloudKit representation.
public enum CloudKitRecordKind: String, Codable, CaseIterable, Sendable {
    case operation = "ProgressOperation"
    case snapshot = "ProgressSnapshot"
    case issue = "QuestionIssue"
    case reviewEvent = "QuestionReviewEvent"

    public var recordType: String { rawValue }
}

public enum CloudKitContract {
    public static let zoneName = "QuizzlerProgress-v1"
    public static let subscriptionID = "QuizzlerProgress-v1-subscription"
    public static let snapshotRecordName = "ProgressSnapshot/current"
    public static let maximumRecordsPerBatch = 250
    /// Reports are independent queue entries, not part of progress history.
    /// A successful acknowledgement removes one entry from the local queue;
    /// refusing a new entry is safer than silently dropping an unsent report.
    public static let maximumQueuedIssues = 128
    /// Keep the payload at roughly half of CloudKit's one-megabyte record
    /// budget. Data fields are base64 encoded by Codable, so the complete
    /// mapped-record check below also has meaningful metadata headroom.
    public static let maximumSnapshotPayloadBytes = 512 * 1024
    public static let maximumSnapshotRecordBytes = 768 * 1024

    public static func recordName(for kind: CloudKitRecordKind, identifier: String) throws -> String {
        guard !identifier.isEmpty,
              !identifier.contains("/"),
              !identifier.contains("\\") else {
            throw CloudKitMappingError.invalidRecordName
        }
        return "\(kind.rawValue)/\(identifier)"
    }

    /// Length-prefixing keeps the index key injective even when a component
    /// contains the separators used by a human-readable question ID.
    public static func reviewQueryKey(for identity: QuestionIdentity) throws -> String {
        let components = [identity.courseID, identity.packID, identity.questionID]
        guard components.allSatisfy({ !$0.isEmpty }) else {
            throw CloudKitMappingError.invalidField("question_identity")
        }
        return components.map { "\($0.utf8.count):\($0)" }.joined(separator: "|")
    }
}

/// A small, deterministic record representation used by tests and by the
/// transport boundary. It avoids constructing a live CloudKit record in unit
/// tests while retaining the exact record type, name, and fields.
public enum CloudKitFieldValue: Codable, Equatable, Sendable, Hashable {
    case string(String)
    case integer(Int64)
    case boolean(Bool)
    case date(Date)
    case data(Data)
}

public struct CloudKitMappedRecord: Codable, Equatable, Sendable, Hashable {
    public let kind: CloudKitRecordKind
    public let recordName: String
    public let fields: [String: CloudKitFieldValue]

    public init(
        kind: CloudKitRecordKind,
        recordName: String,
        fields: [String: CloudKitFieldValue]
    ) throws {
        guard !recordName.isEmpty else { throw CloudKitMappingError.invalidRecordName }
        self.kind = kind
        self.recordName = recordName
        self.fields = fields
    }

    public var recordType: String { kind.recordType }
}

public enum CloudKitMappingError: Error, Codable, Equatable, Sendable {
    case invalidRecordName
    case unsupportedSchemaVersion(Int64)
    case incompatibleVersion(Int64)
    case missingField(String)
    case unknownField(String)
    case invalidField(String)
    case recordTypeMismatch
    case payloadMismatch
    case encodedSizeRefused
}

/// Maps the native models to the private-zone wire records. Every record has
/// a version and stable identity outside its opaque payload. The payload is
/// encoded as Data so adding a field to a domain model cannot accidentally
/// alter the CloudKit field contract; the decoder still rejects unknown
/// CloudKit fields and incompatible schema versions before decoding it.
public enum CloudKitMapping {
    public static let schemaVersion: Int64 = 1
    /// Version two is reserved for records carrying the synchronized Leitner
    /// limit. A v1 client rejects those records before decoding their payload.
    private static let synchronizedLeitnerSchemaVersion: Int64 = 2

    private static let operationFields: Set<String> = [
        "schema_version", "operation_id", "server_revision", "event_count", "event_digest", "created_at", "updated_at", "status", "payload"
    ]
    private static let snapshotFields: Set<String> = [
        "schema_version", "document_revision", "actor_id", "compaction_watermark_revision", "payload"
    ]
    private static let issueFields: Set<String> = [
        "schema_version", "issue_id", "course_id", "pack_id", "question_id",
        "question_type", "app_version", "build", "selected_response", "description"
    ]
    private static let reviewEventFields: Set<String> = [
        "schema_version", "event_id", "operation_id", "ordinal",
        "course_id", "pack_id", "question_id", "question_key",
        "event_time", "outcome", "prior_level", "resulting_level",
        "resulting_due_at", "server_revision", "payload"
    ]

    public static func operationRecord(
        _ operation: ProgressOperation,
        serverRevision: Int? = nil
    ) throws -> CloudKitMappedRecord {
        guard operation.hasValidPayload else { throw CloudKitMappingError.payloadMismatch }
        let recordName = try CloudKitContract.recordName(for: .operation, identifier: operation.id)
        let revision = serverRevision ?? operation.serverRevision
        var fields: [String: CloudKitFieldValue] = [
            "schema_version": .integer(operation.kind == .setMaximumLeitnerLevel ? synchronizedLeitnerSchemaVersion : schemaVersion),
            "operation_id": .string(operation.id),
            "created_at": .date(operation.createdAt),
            "updated_at": .date(operation.updatedAt),
            "status": .string(operation.status.rawValue),
            "payload": .data(try encode(operation))
        ]
        if let revision { fields["server_revision"] = .integer(Int64(revision)) }
        return try CloudKitMappedRecord(
            kind: .operation,
            recordName: recordName,
            fields: fields
        )
    }

    public static func snapshotRecord(_ envelope: ProgressEnvelope) throws -> CloudKitMappedRecord {
        guard (1...ProgressEnvelope.currentSchemaVersion).contains(envelope.schemaVersion),
              envelope.schemaVersion != 1 || !requiresSynchronizedLeitnerSchema(envelope) else {
            throw CloudKitMappingError.payloadMismatch
        }
        var sanitizedEnvelope = envelope
        sanitizedEnvelope.issues = []
        let payload = try encode(sanitizedEnvelope)
        guard payload.count <= CloudKitContract.maximumSnapshotPayloadBytes else {
            throw CloudKitMappingError.encodedSizeRefused
        }
        let record = try CloudKitMappedRecord(
            kind: .snapshot,
            recordName: CloudKitContract.snapshotRecordName,
            fields: [
                "schema_version": .integer(Int64(sanitizedEnvelope.schemaVersion)),
                "document_revision": .integer(Int64(sanitizedEnvelope.documentRevision)),
                "actor_id": .string(sanitizedEnvelope.actorID),
                "compaction_watermark_revision": .integer(Int64(sanitizedEnvelope.compaction.watermarkRevision)),
                "payload": .data(payload)
            ]
        )
        guard try JSONEncoder().encode(record).count <= CloudKitContract.maximumSnapshotRecordBytes else {
            throw CloudKitMappingError.encodedSizeRefused
        }
        return record
    }

    public static func issueRecord(_ issue: QuestionIssue) throws -> CloudKitMappedRecord {
        let recordName = try CloudKitContract.recordName(for: .issue, identifier: issue.issueID)
        var fields: [String: CloudKitFieldValue] = [
            "schema_version": .integer(schemaVersion),
            "issue_id": .string(issue.issueID),
            "course_id": .string(issue.courseID),
            "pack_id": .string(issue.packID),
            "question_id": .string(issue.questionID),
            "question_type": .string(issue.questionType.rawValue),
            "app_version": .string(issue.appVersion),
            "build": .string(issue.build),
            "description": .string(issue.description)
        ]
        if let selectedResponse = issue.selectedResponse {
            fields["selected_response"] = .string(selectedResponse)
        }
        return try CloudKitMappedRecord(kind: .issue, recordName: recordName, fields: fields)
    }

    /// Event records are immutable v2 rows indexed by their complete question
    /// identity. They intentionally never appear in `ProgressEnvelope`.
    public static func reviewEventRecord(_ event: QuestionReviewEvent) throws -> CloudKitMappedRecord {
        try validate(event)
        let eventID = event.id
        let recordName = try CloudKitContract.recordName(for: .reviewEvent, identifier: eventID)
        let identity = event.identity
        return try CloudKitMappedRecord(
            kind: .reviewEvent,
            recordName: recordName,
            fields: [
                "schema_version": .integer(synchronizedLeitnerSchemaVersion),
                "event_id": .string(eventID),
                "operation_id": .string(event.operationID),
                "ordinal": .integer(Int64(event.ordinal)),
                "course_id": .string(identity.courseID),
                "pack_id": .string(identity.packID),
                "question_id": .string(identity.questionID),
                "question_key": .string(try CloudKitContract.reviewQueryKey(for: identity)),
                "event_time": .date(event.eventTime),
                "outcome": .string(event.outcome.rawValue),
                "prior_level": .integer(Int64(event.priorLevel)),
                "resulting_level": .integer(Int64(event.resultingLevel)),
                "resulting_due_at": .date(event.resultingDueAt),
                "server_revision": .integer(Int64(try requiredServerRevision(event))),
                "payload": .data(try encode(event))
            ]
        )
    }

    /// Binds a cap operation to the exact immutable event payloads committed
    /// beside it. Length prefixes make concatenation unambiguous.
    public static func reviewEventDigest(_ records: [CloudKitMappedRecord]) throws -> String {
        var bytes = Data()
        for record in records {
            guard record.kind == .reviewEvent,
                  case let .data(payload)? = record.fields["payload"] else {
                throw CloudKitMappingError.payloadMismatch
            }
            for part in [Data(record.recordName.utf8), payload] {
                var length = UInt64(part.count).bigEndian
                withUnsafeBytes(of: &length) { bytes.append(contentsOf: $0) }
                bytes.append(part)
            }
        }
        return SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
    }

    public static func operation(from record: CloudKitMappedRecord) throws -> ProgressOperation {
        try require(record, kind: .operation, fields: operationFields)
        let operationID = try string(record, field: "operation_id")
        guard record.recordName == (try CloudKitContract.recordName(for: .operation, identifier: operationID)) else {
            throw CloudKitMappingError.invalidRecordName
        }
        let payload = try data(record, field: "payload")
        let operation = try decode(ProgressOperation.self, from: payload)
        let schema = try integer(record, field: "schema_version")
        guard operation.hasValidPayload,
              operation.kind != .setMaximumLeitnerLevel || schema == synchronizedLeitnerSchemaVersion else {
            throw CloudKitMappingError.payloadMismatch
        }
        if let value = record.fields["server_revision"] {
            guard case let .integer(revision) = value, revision > 0 else {
                throw CloudKitMappingError.invalidField("server_revision")
            }
            guard operation.serverRevision == nil || operation.serverRevision == Int(revision) else {
                throw CloudKitMappingError.payloadMismatch
            }
            var revised = operation
            revised.serverRevision = Int(revision)
            return revised
        }
        guard operation.id == operationID,
              operation.createdAt == (try date(record, field: "created_at")),
              operation.updatedAt == (try date(record, field: "updated_at")),
              operation.status.rawValue == (try string(record, field: "status")) else {
            throw CloudKitMappingError.payloadMismatch
        }
        return operation
    }

    public static func snapshot(from record: CloudKitMappedRecord) throws -> ProgressEnvelope {
        try require(record, kind: .snapshot, fields: snapshotFields)
        guard record.recordName == CloudKitContract.snapshotRecordName else {
            throw CloudKitMappingError.invalidRecordName
        }
        let payload = try data(record, field: "payload")
        var envelope = try decode(ProgressEnvelope.self, from: payload)
        let schema = try integer(record, field: "schema_version")
        guard schema == envelope.schemaVersion else {
            throw CloudKitMappingError.payloadMismatch
        }
        guard envelope.documentRevision == Int(try integer(record, field: "document_revision")),
              envelope.actorID == (try string(record, field: "actor_id")),
              envelope.compaction.watermarkRevision == Int(try integer(record, field: "compaction_watermark_revision")) else {
            throw CloudKitMappingError.payloadMismatch
        }
        envelope.issues = []
        return envelope
    }

    public static func issue(from record: CloudKitMappedRecord) throws -> QuestionIssue {
        try require(record, kind: .issue, fields: issueFields)
        let schema = try integer(record, field: "schema_version")
        guard schema == schemaVersion else { throw versionError(schema) }
        let issueID = try string(record, field: "issue_id")
        guard record.recordName == (try CloudKitContract.recordName(for: .issue, identifier: issueID)) else {
            throw CloudKitMappingError.invalidRecordName
        }
        let selectedResponse: String?
        if let value = record.fields["selected_response"] {
            guard case let .string(response) = value else { throw CloudKitMappingError.invalidField("selected_response") }
            selectedResponse = response
        } else {
            selectedResponse = nil
        }
        do {
            guard let questionType = QuestionType(rawValue: try string(record, field: "question_type")) else {
                throw CloudKitMappingError.invalidField("question_type")
            }
            return try QuestionIssue(
                issueID: issueID,
                courseID: try string(record, field: "course_id"),
                packID: try string(record, field: "pack_id"),
                questionID: try string(record, field: "question_id"),
                questionType: questionType,
                appVersion: try string(record, field: "app_version"),
                build: try string(record, field: "build"),
                selectedResponse: selectedResponse,
                description: try string(record, field: "description")
            )
        } catch let error as CloudKitMappingError {
            throw error
        } catch {
            throw CloudKitMappingError.invalidField("issue")
        }
    }

    public static func reviewEvent(from record: CloudKitMappedRecord) throws -> QuestionReviewEvent {
        try require(record, kind: .reviewEvent, fields: reviewEventFields)
        let schema = try integer(record, field: "schema_version")
        guard schema == synchronizedLeitnerSchemaVersion else { throw versionError(schema) }
        let eventID = try string(record, field: "event_id")
        let operationID = try string(record, field: "operation_id")
        let ordinal = try integer(record, field: "ordinal")
        guard ordinal >= 0, ordinal <= Int64(Int.max),
              eventID == "\(operationID):\(ordinal)",
              record.recordName == (try CloudKitContract.recordName(for: .reviewEvent, identifier: eventID)) else {
            throw CloudKitMappingError.invalidRecordName
        }
        let identity = QuestionIdentity(
            courseID: try string(record, field: "course_id"),
            packID: try string(record, field: "pack_id"),
            questionID: try string(record, field: "question_id")
        )
        guard try string(record, field: "question_key") == CloudKitContract.reviewQueryKey(for: identity),
              let outcome = QuestionReviewOutcome(rawValue: try string(record, field: "outcome")) else {
            throw CloudKitMappingError.payloadMismatch
        }
        let revision = try integer(record, field: "server_revision")
        guard revision > 0, revision <= Int64(Int.max) else {
            throw CloudKitMappingError.invalidField("server_revision")
        }
        let event = QuestionReviewEvent(
            operationID: operationID,
            ordinal: Int(ordinal),
            identity: identity,
            eventTime: try date(record, field: "event_time"),
            outcome: outcome,
            priorLevel: try int(record, field: "prior_level"),
            resultingLevel: try int(record, field: "resulting_level"),
            resultingDueAt: try date(record, field: "resulting_due_at"),
            serverRevision: Int(revision)
        )
        try validate(event)
        let payload = try data(record, field: "payload")
        let decoded = try decode(QuestionReviewEvent.self, from: payload)
        guard decoded == event else { throw CloudKitMappingError.payloadMismatch }
        return event
    }

    // Verb-first aliases make the mapping boundary convenient for callers.
    public static func mapOperation(_ operation: ProgressOperation) throws -> CloudKitMappedRecord { try operationRecord(operation) }
    public static func mapSnapshot(_ envelope: ProgressEnvelope) throws -> CloudKitMappedRecord { try snapshotRecord(envelope) }
    public static func mapIssue(_ issue: QuestionIssue) throws -> CloudKitMappedRecord { try issueRecord(issue) }
    public static func mapReviewEvent(_ event: QuestionReviewEvent) throws -> CloudKitMappedRecord { try reviewEventRecord(event) }

    public static func decodeOperation(_ record: CloudKitMappedRecord) throws -> ProgressOperation { try operation(from: record) }
    public static func decodeSnapshot(_ record: CloudKitMappedRecord) throws -> ProgressEnvelope { try snapshot(from: record) }
    public static func decodeIssue(_ record: CloudKitMappedRecord) throws -> QuestionIssue { try issue(from: record) }
    public static func decodeReviewEvent(_ record: CloudKitMappedRecord) throws -> QuestionReviewEvent { try reviewEvent(from: record) }

    public static func decode(_ record: CloudKitMappedRecord) throws -> CloudKitDecodedRecord {
        switch record.kind {
        case .operation: return .operation(try operation(from: record))
        case .snapshot: return .snapshot(try snapshot(from: record))
        case .issue: return .issue(try issue(from: record))
        case .reviewEvent: return .reviewEvent(try reviewEvent(from: record))
        }
    }

    private static func require(
        _ record: CloudKitMappedRecord,
        kind: CloudKitRecordKind,
        fields: Set<String>
    ) throws {
        guard record.kind == kind else { throw CloudKitMappingError.recordTypeMismatch }
        if let unknown = record.fields.keys.first(where: { !fields.contains($0) }) {
            throw CloudKitMappingError.unknownField(unknown)
        }
        for field in fields {
            let optional = (kind == .issue && field == "selected_response")
                || (kind == .operation && (field == "server_revision" || field == "event_count" || field == "event_digest"))
            if !optional && record.fields[field] == nil {
                throw CloudKitMappingError.missingField(field)
            }
        }
        let schema = try integer(record, field: "schema_version")
        guard schema == schemaVersion || (kind != .issue && schema == synchronizedLeitnerSchemaVersion) else {
            throw versionError(schema)
        }
    }

    private static func string(_ record: CloudKitMappedRecord, field: String) throws -> String {
        guard case let .string(value)? = record.fields[field], !value.isEmpty else {
            throw CloudKitMappingError.invalidField(field)
        }
        return value
    }

    private static func integer(_ record: CloudKitMappedRecord, field: String) throws -> Int64 {
        guard case let .integer(value)? = record.fields[field] else {
            throw CloudKitMappingError.invalidField(field)
        }
        return value
    }

    private static func int(_ record: CloudKitMappedRecord, field: String) throws -> Int {
        let value = try integer(record, field: field)
        guard value >= Int64(Int.min), value <= Int64(Int.max) else {
            throw CloudKitMappingError.invalidField(field)
        }
        return Int(value)
    }

    private static func date(_ record: CloudKitMappedRecord, field: String) throws -> Date {
        guard case let .date(value)? = record.fields[field] else {
            throw CloudKitMappingError.invalidField(field)
        }
        return value
    }

    private static func data(_ record: CloudKitMappedRecord, field: String) throws -> Data {
        guard case let .data(value)? = record.fields[field] else {
            throw CloudKitMappingError.invalidField(field)
        }
        return value
    }

    private static func versionError(_ version: Int64) -> CloudKitMappingError {
        version > schemaVersion ? .incompatibleVersion(version) : .unsupportedSchemaVersion(version)
    }

    private static func requiresSynchronizedLeitnerSchema(_ envelope: ProgressEnvelope) -> Bool {
        envelope.maximumLeitnerLevel != 5
            || envelope.operations.contains { $0.kind == .setMaximumLeitnerLevel }
    }

    private static func encode<T: Encodable>(_ value: T) throws -> Data {
        return try JSONEncoder().encode(value)
    }

    private static func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        return try JSONDecoder().decode(type, from: data)
    }

    private static func requiredServerRevision(_ event: QuestionReviewEvent) throws -> Int {
        guard let revision = event.serverRevision, revision > 0 else {
            throw CloudKitMappingError.invalidField("server_revision")
        }
        return revision
    }

    private static func validate(_ event: QuestionReviewEvent) throws {
        guard !event.operationID.isEmpty,
              event.ordinal >= 0,
              event.id == "\(event.operationID):\(event.ordinal)",
              (1...7).contains(event.priorLevel),
              (1...7).contains(event.resultingLevel),
              event.serverRevision != nil else {
            throw CloudKitMappingError.payloadMismatch
        }
        _ = try CloudKitContract.reviewQueryKey(for: event.identity)
        _ = try requiredServerRevision(event)
    }
}

public enum CloudKitDecodedRecord: Sendable, Equatable {
    case operation(ProgressOperation)
    case snapshot(ProgressEnvelope)
    case issue(QuestionIssue)
    case reviewEvent(QuestionReviewEvent)
}

#if canImport(CloudKit)
@available(iOS 17.0, macOS 14.0, *)
public extension CloudKitMappedRecord {
    func makeCKRecord(in zoneID: CKRecordZone.ID) throws -> CKRecord {
        let record = CKRecord(recordType: kind.recordType, recordID: CKRecord.ID(recordName: recordName, zoneID: zoneID))
        for (key, value) in fields {
            switch value {
            case let .string(value): record[key] = value as NSString
            case let .integer(value): record[key] = NSNumber(value: value)
            case let .boolean(value): record[key] = NSNumber(value: value)
            case let .date(value): record[key] = value as NSDate
            case let .data(value): record[key] = value as NSData
            }
        }
        return record
    }

    init(ckRecord: CKRecord) throws {
        guard let kind = CloudKitRecordKind(rawValue: ckRecord.recordType) else {
            throw CloudKitMappingError.recordTypeMismatch
        }
        var fields: [String: CloudKitFieldValue] = [:]
        for key in ckRecord.allKeys() {
            guard let value = ckRecord[key] else { continue }
            if let value = value as? String {
                fields[key] = .string(value)
            } else if let value = value as? Date {
                fields[key] = .date(value)
            } else if let value = value as? Data {
                fields[key] = .data(value)
            } else if let value = value as? NSNumber {
                fields[key] = .integer(value.int64Value)
            } else {
                throw CloudKitMappingError.invalidField(key)
            }
        }
        try self.init(kind: kind, recordName: ckRecord.recordID.recordName, fields: fields)
    }
}
#endif
