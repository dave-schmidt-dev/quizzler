import Foundation
import SwiftUI
import QuizzlerKit
import UIKit

// MARK: - Session Persistence (C3)

extension LaunchpadView {
    /// The key the selected pack's session is stored under: `courseID/packID`.
    var savedSessionPackKey: String? {
        guard let pack = catalog.pack else { return nil }
        return "\(pack.courseID)/\(pack.packID)"
    }

    /// Re-runs the resume check whenever progress and the catalog settle, and
    /// again whenever the selected pack changes. `nil` keeps the launch task
    /// idle until there is something to check.
    var resumeRefreshKey: String? {
        guard progress.isReadyForStudy, let packKey = catalog.selectedPackKey else { return nil }
        return packKey
    }

    /// Writes the running session to the device-local store so leaving or
    /// relaunching the app can offer to continue it. The baseline is the one
    /// captured when the session began, so a later resume can tell which
    /// questions were answered somewhere else in the meantime.
    func persistSession() {
        guard let session = activeSession, let pack = catalog.pack,
              state == .question || state == .feedback else { return }
        sessionStore.save(PersistedSession(
            courseID: pack.courseID,
            packID: pack.packID,
            packFingerprint: PackFingerprint.make(for: pack),
            mode: session.mode,
            plan: session.questions.map(\.identity),
            position: session.position,
            answers: session.answers,
            newIdentities: Array(session.newIdentities),
            startBaseline: sessionStartBaseline,
            scheduledStates: scheduledReviewStates
                .map { ScheduledEntry(identity: $0.key, state: $0.value) }
                .sorted { $0.identity.description < $1.identity.description }
        ))
    }

    /// Removes the selected pack's saved session — once a plan is finished
    /// there is nothing left to offer resuming.
    func clearSavedSession() {
        guard let packKey = savedSessionPackKey else { return }
        sessionStore.clear(packKey: packKey)
        resumable = nil
    }

    /// Re-validates the selected pack's saved session against the current
    /// pack and progress, keeping the outcome for Today's resume row. A
    /// session that can no longer continue is deleted.
    func refreshResumeCandidate() {
        guard let packKey = savedSessionPackKey, progress.isReadyForStudy else {
            resumable = nil
            return
        }
        guard let saved = sessionStore.load(packKey: packKey) else {
            resumable = nil
            return
        }
        switch resolveSavedSession(saved) {
        case .discard:
            sessionStore.clear(packKey: packKey)
            resumable = nil
        case .resumable(let candidate):
            resumable = candidate
        }
    }

    /// Today's resume row title, or `nil` when the selected pack has no
    /// session worth offering.
    var resumeLabel: String? {
        guard let candidate = resumable else { return nil }
        return "Resume session · \(candidate.position + 1) of \(candidate.plan.count)"
    }

    /// Continues the selected pack's saved session. Resume never records
    /// progress; it only rebuilds the in-memory session from the plan that
    /// survived re-validation, reusing the baseline captured when the session
    /// began so later saves stay comparable.
    func resumeSession() {
        guard let packKey = savedSessionPackKey,
              let saved = sessionStore.load(packKey: packKey) else { return }
        switch resolveSavedSession(saved) {
        case .discard:
            sessionStore.clear(packKey: packKey)
            resumable = nil
        case .resumable(let candidate):
            // Map the surviving plan back to real questions, dropping any
            // identity the installed pack no longer carries. The position
            // follows the entries that survive the mapping.
            let questions = catalog.questions
            var sessionQuestions: [StudyQuestion] = []
            var position = 0
            for (planIndex, identity) in candidate.plan.enumerated() {
                guard let question = questions.first(where: { $0.identity == identity }) else { continue }
                if planIndex < candidate.position { position += 1 }
                sessionQuestions.append(question)
            }
            guard sessionQuestions.indices.contains(position) else {
                sessionStore.clear(packKey: packKey)
                resumable = nil
                return
            }
            sessionStartBaseline = saved.startBaseline
            activeSession = ActiveSession(
                mode: candidate.mode,
                questions: sessionQuestions,
                position: position,
                answers: candidate.answers,
                newIdentities: Set(candidate.newIdentities)
            )
            scheduledReviewStates = Dictionary(
                candidate.scheduledStates.map { ($0.identity, $0.state) },
                uniquingKeysWith: { current, _ in current }
            )
            resumable = nil
            selection = .none
            state = .question
        }
    }

    /// Runs the pure re-validation with the counts and due dates the current
    /// progress envelope derives.
    private func resolveSavedSession(_ saved: PersistedSession) -> SessionResume.Resolution {
        guard let pack = catalog.pack else {
            return .discard(reason: "pack_unavailable")
        }
        return SessionResume.resolve(
            saved: saved,
            currentFingerprint: PackFingerprint.make(for: pack),
            catalog: Set(catalog.questions.map(\.identity)),
            answeredCounts: currentAnsweredCounts,
            dueDates: currentDueDates,
            now: Date()
        )
    }

    /// Per-question answered counts from the same sources `seenIdentities`
    /// uses: durable mastery plus pending unsaved answers.
    var currentAnsweredCounts: [QuestionIdentity: Int] {
        var counts: [QuestionIdentity: Int] = [:]
        for mastery in progress.envelope?.mastery ?? [] {
            counts[mastery.identity] = mastery.answered
        }
        for answer in progress.unsavedAnswers {
            counts[answer.identity, default: 0] += 1
        }
        return counts
    }

    /// Per-question SRS due dates, so a saved `.srs` session drops questions
    /// whose review is no longer due.
    private var currentDueDates: [QuestionIdentity: Date] {
        var dates: [QuestionIdentity: Date] = [:]
        for entry in progress.envelope?.srs ?? [] {
            dates[entry.identity] = entry.state.nextDueAt
        }
        return dates
    }
}
