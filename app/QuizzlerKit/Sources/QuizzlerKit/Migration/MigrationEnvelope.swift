import CryptoKit
import Foundation

/// Terminal source dispositions recorded by the attended inventory checkpoint.
public enum MigrationSourcePath: String, Codable, Sendable, Equatable {
    case oneSource = "one_source"
    case multiSource = "multi_source"
    case newStart = "new_start"
}

public struct MigrationSourceCounts: Codable, Sendable, Equatable {
    public let sources: Int
    public let records: Int

    public init(sources: Int, records: Int) throws {
        guard sources >= 0, records >= 0 else { throw MigrationEnvelopeError.invalidCounts }
        self.sources = sources
        self.records = records
    }
}

/// The CloudKit action is a description until the native repository performs
/// its conditional write. Python migration tooling never executes it.
public struct MigrationCloudKitBaseline: Codable, Sendable, Equatable {
    public let documentRevision: Int
    public let operationID: String
    public let semanticHash: String
    public let importClaim: Bool

    public init(
        documentRevision: Int = 0,
        operationID: String = "baseline",
        semanticHash: String,
        importClaim: Bool = false
    ) throws {
        guard documentRevision >= 0,
              !operationID.isEmpty,
              Self.isSHA256(semanticHash) else {
            throw MigrationEnvelopeError.invalidBaseline
        }
        self.documentRevision = documentRevision
        self.operationID = operationID
        self.semanticHash = semanticHash
        self.importClaim = importClaim
    }

    private enum CodingKeys: String, CodingKey {
        case documentRevision = "document_revision"
        case operationID = "operation_id"
        case semanticHash = "semantic_hash"
        case importClaim = "import_claim"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        try self.init(
            documentRevision: container.decode(Int.self, forKey: .documentRevision),
            operationID: container.decode(String.self, forKey: .operationID),
            semanticHash: container.decode(String.self, forKey: .semanticHash),
            importClaim: container.decode(Bool.self, forKey: .importClaim)
        )
    }

    fileprivate static func isSHA256(_ value: String) -> Bool {
        value.count == 64 && value.allSatisfy { $0.isHexDigit }
    }
}

public enum MigrationEnvelopeError: Error, Codable, Sendable, Equatable {
    case invalidEpoch
    case invalidHash
    case invalidCounts
    case invalidScope
    case invalidBaseline
    case newStartMustBeEmpty
    case sourceDataNotAllowed
}

/// Versioned migration evidence. A new-start envelope intentionally contains
/// no source export and binds only an empty native progress baseline.
public struct MigrationEnvelope: Codable, Sendable, Equatable {
    public let schemaVersion: Int
    public let migrationEpoch: String
    public let path: MigrationSourcePath
    public let sourceSnapshotHash: String
    public let sourceExportHash: String?
    public let counts: MigrationSourceCounts
    public let activePackIDs: [String]
    public let progress: ProgressEnvelope
    public let cloudKitBaseline: MigrationCloudKitBaseline

    private enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case migrationEpoch = "migration_epoch"
        case path
        case sourceSnapshotHash = "source_snapshot_hash"
        case sourceExportHash = "source_export_hash"
        case counts
        case scope
        case document
        case cloudKitBaseline = "cloudkit_baseline"
    }

    private struct Scope: Codable, Sendable, Equatable {
        let activePackIDs: [String]

        enum CodingKeys: String, CodingKey {
            case activePackIDs = "active_pack_ids"
        }
    }

    public init(
        schemaVersion: Int = 1,
        migrationEpoch: String,
        path: MigrationSourcePath,
        sourceSnapshotHash: String,
        sourceExportHash: String? = nil,
        counts: MigrationSourceCounts,
        activePackIDs: [String],
        progress: ProgressEnvelope,
        cloudKitBaseline: MigrationCloudKitBaseline
    ) throws {
        guard schemaVersion == 1, !migrationEpoch.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw MigrationEnvelopeError.invalidEpoch
        }
        guard MigrationCloudKitBaseline.isSHA256(sourceSnapshotHash),
              sourceExportHash.map(MigrationCloudKitBaseline.isSHA256) ?? true else {
            throw MigrationEnvelopeError.invalidHash
        }
        guard !activePackIDs.isEmpty,
              activePackIDs.allSatisfy({ !$0.isEmpty }),
              Set(activePackIDs).count == activePackIDs.count else {
            throw MigrationEnvelopeError.invalidScope
        }
        if path == .newStart {
            let emptyCounts = try MigrationSourceCounts(sources: 0, records: 0)
            guard counts == emptyCounts,
                  sourceExportHash == nil,
                  progress.documentRevision == 0,
                  progress.operations.isEmpty,
                  progress.sessionDetails.isEmpty,
                  progress.aggregate == AggregateSnapshot(),
                  progress.mastery.isEmpty,
                  progress.srs.isEmpty,
                  progress.issues.isEmpty,
                  cloudKitBaseline.documentRevision == 0,
                  cloudKitBaseline.importClaim == false else {
                throw MigrationEnvelopeError.newStartMustBeEmpty
            }
        }
        self.schemaVersion = schemaVersion
        self.migrationEpoch = migrationEpoch
        self.path = path
        self.sourceSnapshotHash = sourceSnapshotHash
        self.sourceExportHash = sourceExportHash
        self.counts = counts
        self.activePackIDs = activePackIDs
        self.progress = progress
        self.cloudKitBaseline = cloudKitBaseline
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(schemaVersion, forKey: .schemaVersion)
        try container.encode(migrationEpoch, forKey: .migrationEpoch)
        try container.encode(path, forKey: .path)
        try container.encode(sourceSnapshotHash, forKey: .sourceSnapshotHash)
        try container.encode(sourceExportHash, forKey: .sourceExportHash)
        try container.encode(counts, forKey: .counts)
        try container.encode(Scope(activePackIDs: activePackIDs), forKey: .scope)
        try container.encode(progress, forKey: .document)
        try container.encode(cloudKitBaseline, forKey: .cloudKitBaseline)
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let scope = try container.decode(Scope.self, forKey: .scope)
        try self.init(
            schemaVersion: container.decode(Int.self, forKey: .schemaVersion),
            migrationEpoch: container.decode(String.self, forKey: .migrationEpoch),
            path: container.decode(MigrationSourcePath.self, forKey: .path),
            sourceSnapshotHash: container.decode(String.self, forKey: .sourceSnapshotHash),
            sourceExportHash: container.decodeIfPresent(String.self, forKey: .sourceExportHash),
            counts: container.decode(MigrationSourceCounts.self, forKey: .counts),
            activePackIDs: scope.activePackIDs,
            progress: container.decode(ProgressEnvelope.self, forKey: .document),
            cloudKitBaseline: container.decode(MigrationCloudKitBaseline.self, forKey: .cloudKitBaseline)
        )
    }

    /// Constructs the only supported no-source migration path.
    public static func newStart(
        migrationEpoch: String,
        sourceSnapshotHash: String,
        activePackIDs: [String],
        actorID: String = "migration-baseline"
    ) throws -> MigrationEnvelope {
        let progress = ProgressEnvelope(actorID: actorID, operationID: "baseline")
        let semanticHash = try Self.newStartSemanticHash(
            migrationEpoch: migrationEpoch,
            sourceSnapshotHash: sourceSnapshotHash,
            activePackIDs: activePackIDs
        )
        let baseline = try MigrationCloudKitBaseline(semanticHash: semanticHash)
        return try MigrationEnvelope(
            migrationEpoch: migrationEpoch,
            path: .newStart,
            sourceSnapshotHash: sourceSnapshotHash,
            counts: try MigrationSourceCounts(sources: 0, records: 0),
            activePackIDs: activePackIDs,
            progress: progress,
            cloudKitBaseline: baseline
        )
    }

    public var isNewStart: Bool { path == .newStart }

    public func verifyNewStart() throws {
        guard isNewStart else { throw MigrationEnvelopeError.newStartMustBeEmpty }
        guard counts.sources == 0,
              counts.records == 0,
              sourceExportHash == nil,
              !cloudKitBaseline.importClaim else {
            throw MigrationEnvelopeError.newStartMustBeEmpty
        }
    }

    private struct NewStartHashPayload: Encodable {
        let schemaVersion: Int
        let kind: String
        let migrationEpoch: String
        let sourceSnapshotHash: String
        let activePackIDs: [String]
        let document: EmptyDocument
        let documentRevision: Int
        let operationID: String
        let importClaim: Bool

        enum CodingKeys: String, CodingKey {
            case schemaVersion = "schema_version"
            case kind
            case migrationEpoch = "migration_epoch"
            case sourceSnapshotHash = "source_snapshot_hash"
            case activePackIDs = "active_pack_ids"
            case document
            case documentRevision = "document_revision"
            case operationID = "operation_id"
            case importClaim = "import_claim"
        }
    }

    private struct EmptyDocument: Encodable {
        let schemaVersion: Int = 1
        let sessions: [String] = []
        let mastery: [String: String] = [:]
        let srs: [String: String] = [:]

        enum CodingKeys: String, CodingKey {
            case schemaVersion = "schema_version"
            case sessions, mastery, srs
        }
    }

    private static func newStartSemanticHash(
        migrationEpoch: String,
        sourceSnapshotHash: String,
        activePackIDs: [String]
    ) throws -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let payload = NewStartHashPayload(
            schemaVersion: 1,
            kind: "new_start_baseline",
            migrationEpoch: migrationEpoch,
            sourceSnapshotHash: sourceSnapshotHash,
            activePackIDs: activePackIDs.sorted(),
            document: EmptyDocument(),
            documentRevision: 0,
            operationID: "baseline",
            importClaim: false
        )
        let data = try encoder.encode(payload)
        return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
}
