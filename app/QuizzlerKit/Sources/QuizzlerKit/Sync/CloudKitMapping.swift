import Foundation

#if canImport(CloudKit)
import CloudKit
#endif

/// The only record types used by native progress sync. Packs and question text
/// deliberately have no CloudKit representation.
public enum CloudKitRecordKind: String, Codable, CaseIterable, Sendable {
    case operation = "ProgressOperation"
    case snapshot = "ProgressSnapshot"
    case issue = "QuestionIssue"

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
    case issueQueueLimitExceeded
}

/// Maps the native models to the private-zone wire records. Every record has
/// a version and stable identity outside its opaque payload. The payload is
/// encoded as Data so adding a field to a domain model cannot accidentally
/// alter the CloudKit field contract; the decoder still rejects unknown
/// CloudKit fields and incompatible schema versions before decoding it.
public enum CloudKitMapping {
    public static let schemaVersion: Int64 = 1

    private static let operationFields: Set<String> = [
        "schema_version", "operation_id", "server_revision", "created_at", "updated_at", "status", "payload"
    ]
    private static let snapshotFields: Set<String> = [
        "schema_version", "document_revision", "actor_id", "compaction_watermark_revision", "payload"
    ]
    private static let issueFields: Set<String> = [
        "schema_version", "issue_id", "course_id", "pack_id", "question_id",
        "question_type", "app_version", "build", "selected_response", "description"
    ]

    public static func operationRecord(
        _ operation: ProgressOperation,
        serverRevision: Int? = nil
    ) throws -> CloudKitMappedRecord {
        let recordName = try CloudKitContract.recordName(for: .operation, identifier: operation.id)
        let revision = serverRevision ?? operation.serverRevision
        var fields: [String: CloudKitFieldValue] = [
            "schema_version": .integer(schemaVersion),
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
        guard envelope.issues.count <= CloudKitContract.maximumQueuedIssues else {
            throw CloudKitMappingError.issueQueueLimitExceeded
        }
        let payload = try encode(envelope)
        guard payload.count <= CloudKitContract.maximumSnapshotPayloadBytes else {
            throw CloudKitMappingError.encodedSizeRefused
        }
        let record = try CloudKitMappedRecord(
            kind: .snapshot,
            recordName: CloudKitContract.snapshotRecordName,
            fields: [
                "schema_version": .integer(schemaVersion),
                "document_revision": .integer(Int64(envelope.documentRevision)),
                "actor_id": .string(envelope.actorID),
                "compaction_watermark_revision": .integer(Int64(envelope.compaction.watermarkRevision)),
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

    public static func operation(from record: CloudKitMappedRecord) throws -> ProgressOperation {
        try require(record, kind: .operation, fields: operationFields)
        let operationID = try string(record, field: "operation_id")
        guard record.recordName == (try CloudKitContract.recordName(for: .operation, identifier: operationID)) else {
            throw CloudKitMappingError.invalidRecordName
        }
        let payload = try data(record, field: "payload")
        let operation = try decode(ProgressOperation.self, from: payload)
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
        let envelope = try decode(ProgressEnvelope.self, from: payload)
        guard envelope.documentRevision == Int(try integer(record, field: "document_revision")),
              envelope.actorID == (try string(record, field: "actor_id")),
              envelope.compaction.watermarkRevision == Int(try integer(record, field: "compaction_watermark_revision")) else {
            throw CloudKitMappingError.payloadMismatch
        }
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

    // Verb-first aliases make the mapping boundary convenient for callers.
    public static func mapOperation(_ operation: ProgressOperation) throws -> CloudKitMappedRecord { try operationRecord(operation) }
    public static func mapSnapshot(_ envelope: ProgressEnvelope) throws -> CloudKitMappedRecord { try snapshotRecord(envelope) }
    public static func mapIssue(_ issue: QuestionIssue) throws -> CloudKitMappedRecord { try issueRecord(issue) }

    public static func decodeOperation(_ record: CloudKitMappedRecord) throws -> ProgressOperation { try operation(from: record) }
    public static func decodeSnapshot(_ record: CloudKitMappedRecord) throws -> ProgressEnvelope { try snapshot(from: record) }
    public static func decodeIssue(_ record: CloudKitMappedRecord) throws -> QuestionIssue { try issue(from: record) }

    public static func decode(_ record: CloudKitMappedRecord) throws -> CloudKitDecodedRecord {
        switch record.kind {
        case .operation: return .operation(try operation(from: record))
        case .snapshot: return .snapshot(try snapshot(from: record))
        case .issue: return .issue(try issue(from: record))
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
                || (kind == .operation && field == "server_revision")
            if !optional && record.fields[field] == nil {
                throw CloudKitMappingError.missingField(field)
            }
        }
        let schema = try integer(record, field: "schema_version")
        guard schema == schemaVersion else { throw versionError(schema) }
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

    private static func encode<T: Encodable>(_ value: T) throws -> Data {
        return try JSONEncoder().encode(value)
    }

    private static func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        return try JSONDecoder().decode(type, from: data)
    }
}

public enum CloudKitDecodedRecord: Sendable, Equatable {
    case operation(ProgressOperation)
    case snapshot(ProgressEnvelope)
    case issue(QuestionIssue)
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
