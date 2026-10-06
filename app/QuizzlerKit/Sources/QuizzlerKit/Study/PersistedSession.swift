import Foundation

/// A question's answered count at the moment a session started.
///
/// On resume, a question whose current answered count exceeds this baseline was
/// answered somewhere else and is dropped from the remaining plan.
public struct BaselineEntry: Codable, Equatable, Sendable {
    public let identity: QuestionIdentity
    public let answeredCount: Int

    public init(identity: QuestionIdentity, answeredCount: Int) {
        self.identity = identity
        self.answeredCount = answeredCount
    }

    enum CodingKeys: String, CodingKey {
        case identity
        case answeredCount = "answered_count"
    }
}

/// A question's SRS state captured alongside the session that scheduled it.
public struct ScheduledEntry: Codable, Equatable, Sendable {
    public let identity: QuestionIdentity
    public let state: SRSState

    public init(identity: QuestionIdentity, state: SRSState) {
        self.identity = identity
        self.state = state
    }

    enum CodingKeys: String, CodingKey {
        case identity
        case state
    }
}

/// An in-progress study session persisted device-locally so it survives
/// leaving and relaunching the app.
///
/// This is deliberately not part of the CloudKit progress contract: resume
/// state is per-device, and restoring it never records progress. Resume only
/// reads progress to re-validate the plan (see `SessionResume`).
public struct PersistedSession: Codable, Equatable, Sendable {
    /// Bumped whenever the persisted shape changes in a way an older reader
    /// cannot interpret. A mismatch makes `SessionResume.resolve` discard.
    public static let currentSchemaVersion = 1

    public let schemaVersion: Int
    public let courseID: String
    public let packID: String
    /// The pack the plan was built from (`PackFingerprint.make(for:)`).
    public let packFingerprint: String
    public let mode: SelectionMode
    /// The full ordered plan the session serves. Answered entries stay in it
    /// so the visible "N of M" count remains truthful across a resume.
    public let plan: [QuestionIdentity]
    /// The index into `plan` of the next question to serve.
    public let position: Int
    /// Answers recorded so far. A question in this list is never served again.
    public let answers: [SessionAnswer]
    /// Questions that were new (never previously answered) when served.
    public let newIdentities: [QuestionIdentity]
    public let startedAt: Date
    public let updatedAt: Date
    /// Per-question answered counts captured once at session start.
    public let startBaseline: [BaselineEntry]
    /// SRS states captured with the session, restored alongside it.
    public let scheduledStates: [ScheduledEntry]

    /// The key this session is stored under: `courseID/packID`.
    public var packKey: String { "\(courseID)/\(packID)" }

    public init(
        schemaVersion: Int = PersistedSession.currentSchemaVersion,
        courseID: String,
        packID: String,
        packFingerprint: String,
        mode: SelectionMode,
        plan: [QuestionIdentity] = [],
        position: Int = 0,
        answers: [SessionAnswer] = [],
        newIdentities: [QuestionIdentity] = [],
        startedAt: Date = Date(),
        updatedAt: Date = Date(),
        startBaseline: [BaselineEntry] = [],
        scheduledStates: [ScheduledEntry] = []
    ) {
        self.schemaVersion = schemaVersion
        self.courseID = courseID
        self.packID = packID
        self.packFingerprint = packFingerprint
        self.mode = mode
        self.plan = plan
        self.position = position
        self.answers = answers
        self.newIdentities = newIdentities
        self.startedAt = startedAt
        self.updatedAt = updatedAt
        self.startBaseline = startBaseline
        self.scheduledStates = scheduledStates
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case courseID = "course_id"
        case packID = "pack_id"
        case packFingerprint = "pack_fingerprint"
        case mode
        case plan
        case position
        case answers
        case newIdentities = "new_identities"
        case startedAt = "started_at"
        case updatedAt = "updated_at"
        case startBaseline = "start_baseline"
        case scheduledStates = "scheduled_states"
    }
}
