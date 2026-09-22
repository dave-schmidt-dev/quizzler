import Foundation

#if canImport(CloudKit)
import CloudKit
#endif

// MARK: - Document Models

/// A single issue report stored in the local issue inbox document,
/// tracking when the issue was reported by the user and when it was received on this device.
public struct IssueInboxEntry: Codable, Equatable, Sendable {
    /// The privacy-minimal question issue payload.
    public let issue: QuestionIssue
    /// The timestamp when the report was originally created.
    public let reportedAt: Date
    /// The timestamp when the report was received and ingested by this inbox.
    public let receivedAt: Date

    /// Initializes a new issue inbox entry.
    public init(issue: QuestionIssue, reportedAt: Date, receivedAt: Date) {
        self.issue = issue
        self.reportedAt = reportedAt
        self.receivedAt = receivedAt
    }

    private enum CodingKeys: String, CodingKey {
        case reportedAtMs = "reported_at_ms"
        case receivedAtMs = "received_at_ms"
        case issue
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let reportedAtMs = try container.decode(Int64.self, forKey: .reportedAtMs)
        let receivedAtMs = try container.decode(Int64.self, forKey: .receivedAtMs)
        let issue = try container.decode(QuestionIssue.self, forKey: .issue)
        self.reportedAt = Date(timeIntervalSince1970: TimeInterval(reportedAtMs) / 1000.0)
        self.receivedAt = Date(timeIntervalSince1970: TimeInterval(receivedAtMs) / 1000.0)
        self.issue = issue
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        let reportedAtMs = Int64(reportedAt.timeIntervalSince1970 * 1000.0)
        let receivedAtMs = Int64(receivedAt.timeIntervalSince1970 * 1000.0)
        try container.encode(reportedAtMs, forKey: .reportedAtMs)
        try container.encode(receivedAtMs, forKey: .receivedAtMs)
        try container.encode(issue, forKey: .issue)
    }
}

/// Errors thrown when validating or decoding an issue inbox document.
public enum IssueInboxDocumentError: Error, Equatable, Sendable {
    /// The document protocol string does not match the expected protocol.
    case invalidProtocol(String)
    /// The document schema version is unsupported.
    case unsupportedVersion(Int)
    /// An issue dictionary key does not match the contained issue's `issue_id`.
    case keyMismatch(key: String, issueID: String)
    /// The change token string is not valid base64 data.
    case invalidChangeToken
}

/// The top-level issue inbox export document conforming to `protocol-fixtures/issue-inbox-v1.json`.
public struct IssueInboxDocument: Codable, Equatable, Sendable {
    /// The protocol name identifying the document format.
    public static let expectedProtocol = "quizzler-issue-inbox"
    /// The supported document schema version.
    public static let expectedVersion = 1

    /// The opaque CloudKit server change token from the latest sync page.
    public var changeToken: Data?
    /// All received issue reports keyed by their stable `issue_id`.
    public var issues: [String: IssueInboxEntry]

    /// Initializes a new issue inbox document.
    public init(changeToken: Data? = nil, issues: [String: IssueInboxEntry] = [:]) {
        self.changeToken = changeToken
        self.issues = issues
    }

    private enum CodingKeys: String, CodingKey {
        case protocolName = "protocol"
        case version
        case changeToken = "change_token"
        case issues
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let proto = try container.decode(String.self, forKey: .protocolName)
        guard proto == Self.expectedProtocol else {
            throw IssueInboxDocumentError.invalidProtocol(proto)
        }
        let version = try container.decode(Int.self, forKey: .version)
        guard version == Self.expectedVersion else {
            throw IssueInboxDocumentError.unsupportedVersion(version)
        }
        if let base64 = try container.decodeIfPresent(String.self, forKey: .changeToken) {
            guard let data = Data(base64Encoded: base64) else {
                throw IssueInboxDocumentError.invalidChangeToken
            }
            self.changeToken = data
        } else {
            self.changeToken = nil
        }
        let decodedIssues = try container.decode([String: IssueInboxEntry].self, forKey: .issues)
        for (key, entry) in decodedIssues {
            guard key == entry.issue.issueID else {
                throw IssueInboxDocumentError.keyMismatch(key: key, issueID: entry.issue.issueID)
            }
        }
        self.issues = decodedIssues
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(Self.expectedProtocol, forKey: .protocolName)
        try container.encode(Self.expectedVersion, forKey: .version)
        if let changeToken {
            try container.encode(changeToken.base64EncodedString(), forKey: .changeToken)
        } else {
            try container.encodeNil(forKey: .changeToken)
        }
        try container.encode(issues, forKey: .issues)
    }
}

// MARK: - Source Protocol & Transport Types

/// A record fetched from an issue inbox source, pairing a CloudKit mapped record with its server creation date.
public struct IssueInboxRecord: Sendable, Equatable {
    /// The mapped CloudKit record.
    public let record: CloudKitMappedRecord
    /// The creation timestamp assigned by the CloudKit server, if available.
    public let serverCreationDate: Date?

    /// Initializes a new fetched record item.
    public init(record: CloudKitMappedRecord, serverCreationDate: Date? = nil) {
        self.record = record
        self.serverCreationDate = serverCreationDate
    }
}

/// A single page of record changes returned by an `IssueInboxSource`.
public struct IssueInboxPage: Sendable, Equatable {
    public typealias Record = IssueInboxRecord

    /// The fetched records in this page.
    public let records: [IssueInboxRecord]
    /// The updated change token after this page, or nil if none.
    public let changeToken: Data?
    /// Whether additional change pages remain to be fetched.
    public let moreComing: Bool

    /// Initializes a page with an array of `IssueInboxRecord`.
    public init(records: [IssueInboxRecord], changeToken: Data?, moreComing: Bool) {
        self.records = records
        self.changeToken = changeToken
        self.moreComing = moreComing
    }

    /// Initializes a page with a tuple list of mapped records and optional server creation dates.
    public init(records: [(record: CloudKitMappedRecord, serverCreationDate: Date?)], changeToken: Data?, moreComing: Bool) {
        self.records = records.map { IssueInboxRecord(record: $0.record, serverCreationDate: $0.serverCreationDate) }
        self.changeToken = changeToken
        self.moreComing = moreComing
    }

    /// Convenience initializer for records without creation dates.
    public init(records: [CloudKitMappedRecord], changeToken: Data?, moreComing: Bool) {
        self.records = records.map { IssueInboxRecord(record: $0, serverCreationDate: nil) }
        self.changeToken = changeToken
        self.moreComing = moreComing
    }
}

/// Errors raised by an issue inbox source.
public enum IssueInboxSourceError: Error, Equatable, Sendable {
    /// The provided server change token is expired and sync must restart from nil.
    case changeTokenExpired
    /// The requested record zone does not exist.
    case zoneNotFound
}

/// A transport boundary source for fetching question issue changes from CloudKit.
public protocol IssueInboxSource: Sendable {
    /// Fetches a page of record changes since the provided server change token.
    func fetchChanges(since token: Data?) async throws -> IssueInboxPage
}

// MARK: - CloudKit Source Implementation

#if canImport(CloudKit)
/// A read-only CloudKit source that fetches question issue changes from the private database.
@available(iOS 17.0, macOS 14.0, *)
public final class CloudKitIssueInboxSource: IssueInboxSource, Sendable {
    /// The container identifier used to access CloudKit.
    public let containerIdentifier: String
    private let database: CKDatabase

    /// The issue record field keys fetched from CloudKit.
    public static let issueFieldNames: [String] = [
        "schema_version", "issue_id", "course_id", "pack_id", "question_id",
        "question_type", "app_version", "build", "selected_response", "description"
    ]

    /// Initializes the source with a CloudKit container identifier.
    public init(containerIdentifier: String) {
        self.containerIdentifier = containerIdentifier
        self.database = CKContainer(identifier: containerIdentifier).privateCloudDatabase
    }

    /// Convenience initializer matching label-free identifier conventions.
    public convenience init(_ containerIdentifier: String) {
        self.init(containerIdentifier: containerIdentifier)
    }

    public func fetchChanges(since token: Data?) async throws -> IssueInboxPage {
        let zoneID = CKRecordZone.ID(zoneName: CloudKitContract.zoneName)
        let serverToken: CKServerChangeToken?
        if let token {
            serverToken = try NSKeyedUnarchiver.unarchivedObject(ofClass: CKServerChangeToken.self, from: token)
        } else {
            serverToken = nil
        }

        do {
            let result = try await database.recordZoneChanges(
                inZoneWith: zoneID,
                since: serverToken,
                desiredKeys: Self.issueFieldNames,
                resultsLimit: nil
            )

            var fetchedRecords: [IssueInboxRecord] = []
            for (_, modResult) in result.modificationResultsByID {
                guard case .success(let modification) = modResult else { continue }
                let ckRecord = modification.record
                guard let mapped = try? CloudKitMappedRecord(ckRecord: ckRecord) else { continue }
                fetchedRecords.append(IssueInboxRecord(record: mapped, serverCreationDate: ckRecord.creationDate))
            }

            // Ensure deterministic ordering by creation date then record name
            fetchedRecords.sort { lhs, rhs in
                if let d1 = lhs.serverCreationDate, let d2 = rhs.serverCreationDate, d1 != d2 {
                    return d1 < d2
                }
                return lhs.record.recordName < rhs.record.recordName
            }

            let newChangeToken = try NSKeyedArchiver.archivedData(withRootObject: result.changeToken, requiringSecureCoding: true)
            return IssueInboxPage(records: fetchedRecords, changeToken: newChangeToken, moreComing: result.moreComing)
        } catch {
            throw Self.mapCloudKitError(error)
        }
    }

    private static func mapCloudKitError(_ error: Error) -> Error {
        if let ckError = error as? CKError {
            if ckError.code == .changeTokenExpired {
                return IssueInboxSourceError.changeTokenExpired
            }
            if ckError.code == .zoneNotFound {
                return IssueInboxSourceError.zoneNotFound
            }
            if ckError.code == .partialFailure,
               let partialErrors = ckError.userInfo[CKPartialErrorsByItemIDKey] as? [AnyHashable: Error] {
                for (_, subError) in partialErrors {
                    if let subCKError = subError as? CKError {
                        if subCKError.code == .changeTokenExpired {
                            return IssueInboxSourceError.changeTokenExpired
                        }
                        if subCKError.code == .zoneNotFound {
                            return IssueInboxSourceError.zoneNotFound
                        }
                    }
                }
            }
        }
        return error
    }
}
#endif

// MARK: - Status & Summary Types

/// Categorized failure reasons surfaced by the issue inbox reader.
public enum IssueInboxFailureReason: String, Sendable, Equatable {
    /// The server change token was reported expired and cannot be refreshed.
    case tokenExpired
    /// The target CloudKit record zone was not found.
    case zoneNotFound
    /// The existing local store file is unreadable or malformed.
    case unreadableStore
    /// An underlying source or I/O error occurred.
    case source
}

/// Lifecycle status events emitted by `IssueInboxReader` during a refresh.
public enum IssueInboxStatus: Sendable, Equatable {
    /// Refresh has begun.
    case started
    /// A page of record changes was processed.
    case page(fetched: Int, new: Int)
    /// Refresh finished successfully with summary statistics.
    case finished(IssueInboxRefreshSummary)
    /// Refresh failed with a categorized reason.
    case failed(IssueInboxFailureReason)
}

/// Summary statistics describing the result of a completed issue inbox refresh.
public struct IssueInboxRefreshSummary: Sendable, Equatable {
    /// Number of new issues added during this refresh.
    public let newCount: Int
    /// Total number of issues stored in the document after this refresh.
    public let totalCount: Int
    /// Number of non-issue or malformed records encountered and skipped.
    public let skippedCount: Int

    /// Alias for `newCount`.
    public var new: Int { newCount }
    /// Alias for `totalCount`.
    public var total: Int { totalCount }
    /// Alias for `skippedCount`.
    public var skipped: Int { skippedCount }

    /// Initializes a summary with standard count labels.
    public init(newCount: Int, totalCount: Int, skippedCount: Int) {
        self.newCount = newCount
        self.totalCount = totalCount
        self.skippedCount = skippedCount
    }

    /// Initializes a summary with short count labels.
    public init(new: Int, total: Int, skipped: Int) {
        self.newCount = new
        self.totalCount = total
        self.skippedCount = skipped
    }
}

// MARK: - Issue Inbox Reader Actor

/// An actor that coordinates fetching question issue reports from a source,
/// deduplicating them, and writing them atomically to a local export document.
public actor IssueInboxReader {
    private let source: any IssueInboxSource
    private let fileURL: URL
    private let now: @Sendable () -> Date
    private let onStatus: @Sendable (IssueInboxStatus) -> Void

    /// Initializes an issue inbox reader.
    ///
    /// - Parameters:
    ///   - source: The record source used to fetch changes.
    ///   - fileURL: The local destination file URL where the inbox document is saved.
    ///   - now: Closure returning the current date, defaulting to `Date.init`.
    ///   - onStatus: Callback receiving status events during refresh.
    public init(
        source: any IssueInboxSource,
        fileURL: URL,
        now: @escaping @Sendable () -> Date = Date.init,
        onStatus: @escaping @Sendable (IssueInboxStatus) -> Void = { _ in }
    ) {
        self.source = source
        self.fileURL = fileURL
        self.now = now
        self.onStatus = onStatus
    }

    /// Refreshes the local issue inbox document from the source.
    ///
    /// - Returns: A summary of newly added, total, and skipped issue reports.
    /// - Throws: An error if loading the existing document fails or if the source throws an unrecoverable error.
    public func refresh() async throws -> IssueInboxRefreshSummary {
        onStatus(.started)

        var currentDocument: IssueInboxDocument
        if FileManager.default.fileExists(atPath: fileURL.path) {
            do {
                let data = try Data(contentsOf: fileURL)
                currentDocument = try JSONDecoder().decode(IssueInboxDocument.self, from: data)
            } catch {
                onStatus(.failed(.unreadableStore))
                throw error
            }
        } else {
            currentDocument = IssueInboxDocument(changeToken: nil, issues: [:])
        }

        var token = currentDocument.changeToken
        var totalNewCount = 0
        var totalSkippedCount = 0
        var hasRestartedForExpiredToken = false

        while true {
            let page: IssueInboxPage
            do {
                page = try await source.fetchChanges(since: token)
            } catch let error as IssueInboxSourceError {
                if error == .changeTokenExpired && !hasRestartedForExpiredToken {
                    hasRestartedForExpiredToken = true
                    token = nil
                    continue
                }
                switch error {
                case .changeTokenExpired:
                    onStatus(.failed(.tokenExpired))
                case .zoneNotFound:
                    onStatus(.failed(.zoneNotFound))
                }
                throw error
            } catch {
                onStatus(.failed(.source))
                throw error
            }

            var pageNewCount = 0
            for item in page.records {
                guard item.record.kind == .issue else {
                    totalSkippedCount += 1
                    continue
                }
                let issue: QuestionIssue
                do {
                    issue = try CloudKitMapping.issue(from: item.record)
                } catch {
                    totalSkippedCount += 1
                    continue
                }

                if currentDocument.issues[issue.issueID] == nil {
                    let currentTime = now()
                    let reportedAt = item.serverCreationDate ?? currentTime
                    let entry = IssueInboxEntry(issue: issue, reportedAt: reportedAt, receivedAt: currentTime)
                    currentDocument.issues[issue.issueID] = entry
                    pageNewCount += 1
                    totalNewCount += 1
                }
            }

            currentDocument.changeToken = page.changeToken

            do {
                let encoder = JSONEncoder()
                encoder.outputFormatting = [.sortedKeys, .prettyPrinted]
                let data = try encoder.encode(currentDocument)
                let parentDirectory = fileURL.deletingLastPathComponent()
                try FileManager.default.createDirectory(at: parentDirectory, withIntermediateDirectories: true)
                try data.write(to: fileURL, options: [.atomic])
            } catch {
                onStatus(.failed(.source))
                throw error
            }

            onStatus(.page(fetched: page.records.count, new: pageNewCount))

            if !page.moreComing {
                break
            }
            token = page.changeToken
        }

        let summary = IssueInboxRefreshSummary(
            newCount: totalNewCount,
            totalCount: currentDocument.issues.count,
            skippedCount: totalSkippedCount
        )
        onStatus(.finished(summary))
        return summary
    }
}
