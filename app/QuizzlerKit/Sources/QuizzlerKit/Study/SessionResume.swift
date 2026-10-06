import Foundation

/// Pure re-validation of a persisted session against the current pack and
/// progress state.
///
/// Resume never writes progress; it only decides whether a saved session can
/// continue and what it would serve next. Counts and due dates arrive as plain
/// dictionaries so this stays a pure function; callers derive them from the
/// progress envelope.
public enum SessionResume {

    /// The outcome of re-validating a saved session.
    public enum Resolution: Equatable, Sendable {
        /// The session cannot continue. `reason` is a stable, loggable string:
        /// `"schema_version_mismatch"`, `"pack_fingerprint_mismatch"`, or
        /// `"no_unanswered_questions_remaining"`.
        case discard(reason: String)
        /// The session continues with the surviving plan and next position.
        case resumable(ResumableSession)
    }

    /// A session restored and re-validated for continued study.
    public struct ResumableSession: Equatable, Sendable {
        /// The surviving plan: every answered entry plus every still-servable
        /// unanswered entry, in the original order.
        public let plan: [QuestionIdentity]
        /// The index into `plan` of the next question to serve.
        public let position: Int
        public let answers: [SessionAnswer]
        public let mode: SelectionMode
        public let newIdentities: [QuestionIdentity]
        public let scheduledStates: [ScheduledEntry]

        public init(
            plan: [QuestionIdentity],
            position: Int,
            answers: [SessionAnswer],
            mode: SelectionMode,
            newIdentities: [QuestionIdentity],
            scheduledStates: [ScheduledEntry]
        ) {
            self.plan = plan
            self.position = position
            self.answers = answers
            self.mode = mode
            self.newIdentities = newIdentities
            self.scheduledStates = scheduledStates
        }
    }

    /// Re-validates a saved session. Rules, in order:
    ///
    /// 1. A schema or pack fingerprint mismatch discards the session.
    /// 2. Questions already in `answers` are never served again; the position
    ///    moves to the first plan index at or after the saved position that is
    ///    not in `answers`.
    /// 3. From the remaining unanswered questions, any not in `catalog`, any
    ///    whose current answered count exceeds its `startBaseline` (answered
    ///    elsewhere), and — for `.srs` mode — any whose due date is after
    ///    `now`, are dropped.
    /// 4. Answered entries stay in the plan so "N of M" remains truthful
    ///    (M is the surviving plan length).
    /// 5. When no unanswered question remains, the session discards.
    public static func resolve(
        saved: PersistedSession,
        currentFingerprint: String,
        catalog: Set<QuestionIdentity>,
        answeredCounts: [QuestionIdentity: Int],
        dueDates: [QuestionIdentity: Date],
        now: Date
    ) -> Resolution {
        guard saved.schemaVersion == PersistedSession.currentSchemaVersion else {
            return .discard(reason: "schema_version_mismatch")
        }
        guard saved.packFingerprint == currentFingerprint else {
            return .discard(reason: "pack_fingerprint_mismatch")
        }

        let answered = Set(saved.answers.map(\.identity))
        var baseline: [QuestionIdentity: Int] = [:]
        for entry in saved.startBaseline {
            baseline[entry.identity] = entry.answeredCount
        }

        // Keep every answered entry (the answered prefix) plus the unanswered
        // entries that are still servable, in plan order.
        var surviving: [(planIndex: Int, identity: QuestionIdentity, isAnswered: Bool)] = []
        for (planIndex, identity) in saved.plan.enumerated() {
            if answered.contains(identity) {
                surviving.append((planIndex, identity, true))
                continue
            }
            guard catalog.contains(identity) else { continue }
            if (answeredCounts[identity] ?? 0) > (baseline[identity] ?? 0) { continue }
            if saved.mode == .srs, let due = dueDates[identity], due > now { continue }
            surviving.append((planIndex, identity, false))
        }

        // The position never moves backward and never re-serves an answered
        // question: resume from the first plan index at or after the saved
        // position that has not been answered.
        guard let resumeFrom = saved.plan.indices.first(where: {
            $0 >= saved.position && !answered.contains(saved.plan[$0])
        }) else {
            return .discard(reason: "no_unanswered_questions_remaining")
        }
        guard let position = surviving.firstIndex(where: { !$0.isAnswered && $0.planIndex >= resumeFrom }) else {
            return .discard(reason: "no_unanswered_questions_remaining")
        }

        return .resumable(ResumableSession(
            plan: surviving.map { $0.identity },
            position: position,
            answers: saved.answers,
            mode: saved.mode,
            newIdentities: saved.newIdentities,
            scheduledStates: saved.scheduledStates
        ))
    }
}
