import Foundation
import SwiftUI
import QuizzlerKit
import UIKit

extension LaunchpadView {
    func startSession() {
        let questions = catalog.questions
        guard !questions.isEmpty else { return }
        let catalogMap = Dictionary(uniqueKeysWithValues: questions.map { ($0.identity, $0.question) })
        let limit = sessionLimit(candidateCount: questions.count)
        guard let request = try? SelectionRequest(mode: .normal, limit: limit) else { return }
        let plan = StudySessionPlan.build(
            request: request,
            envelope: progress.envelope,
            catalog: catalogMap,
            packOrder: questions.map(\.identity),
            resumeIndex: resumeIndex(count: questions.count),
            now: Date()
        )
        // Resolve plan identities back to StudyQuestion objects so the session
        // can serve them directly without re-indexing into the pack each step.
        let sessionQuestions = plan.questions.compactMap { identity in
            questions.first { $0.identity == identity }
        }
        guard !sessionQuestions.isEmpty else { return }
        selection = .none
        activeSession = ActiveSession(
            mode: .normal,
            questions: sessionQuestions,
            position: 0,
            answers: [],
            newIdentities: newIdentities(for: sessionQuestions)
        )
        scheduledReviewStates = [:]
        consumeNextSessionLengthOverride()
        state = .question
    }

    func startDueReview() {
        guard scheduledReviewEnabled else { return }
        let questions = catalog.questions
        let limit = sessionLimit(candidateCount: questions.count)
        startModeSession(mode: .srs, count: min(currentInsights.due.due, limit))
    }

    func startRetryMissed() {
        startModeSession(
            mode: .retryMissed,
            count: sessionLimit(candidateCount: currentInsights.recentMisses.count)
        )
    }

    /// Starts a new retryMissed session seeded from the just-finished session's
    /// wrong answers, so the learner re-drills exactly what they missed without
    /// mixing in new SRS-due questions.
    func startRetryMissedFromSession() {
        guard let session = activeSession else { return }
        let wrongIdentities = session.answers.filter { !$0.correct }.map(\.identity)
        guard !wrongIdentities.isEmpty else { return }
        let questions = catalog.questions
        let sessionQuestions = wrongIdentities.prefix(sessionLimit(candidateCount: wrongIdentities.count)).compactMap { identity in
            questions.first { $0.identity == identity }
        }
        guard !sessionQuestions.isEmpty else { return }
        selection = .none
        activeSession = ActiveSession(
            mode: .retryMissed,
            questions: sessionQuestions,
            position: 0,
            answers: [],
            newIdentities: newIdentities(for: sessionQuestions)
        )
        scheduledReviewStates = [:]
        consumeNextSessionLengthOverride()
        state = .question
    }

    private func startModeSession(mode: SelectionMode, count: Int) {
        let questions = catalog.questions
        guard !questions.isEmpty, count > 0 else { return }
        let catalogMap = Dictionary(uniqueKeysWithValues: questions.map { ($0.identity, $0.question) })
        guard let request = try? SelectionRequest(mode: mode, limit: count) else { return }
        let plan = StudySessionPlan.build(
            request: request,
            envelope: progress.envelope,
            catalog: catalogMap,
            packOrder: questions.map(\.identity),
            resumeIndex: resumeIndex(count: questions.count),
            now: Date()
        )
        let sessionQuestions = plan.questions.compactMap { identity in
            questions.first { $0.identity == identity }
        }
        guard !sessionQuestions.isEmpty else { return }
        selection = .none
        activeSession = ActiveSession(
            mode: mode,
            questions: sessionQuestions,
            position: 0,
            answers: [],
            newIdentities: newIdentities(for: sessionQuestions)
        )
        scheduledReviewStates = mode == .srs
            ? Dictionary(uniqueKeysWithValues: sessionQuestions.compactMap { question in
                progress.envelope?.srs.first(where: { $0.identity == question.identity }).map { (question.identity, $0.state) }
            })
            : [:]
        consumeNextSessionLengthOverride()
        state = .question
    }

    private func newIdentities(for questions: [StudyQuestion]) -> Set<QuestionIdentity> {
        guard !questions.isEmpty else { return [] }
        var seen = Set<QuestionIdentity>()
        var queriedPacks = Set<String>()
        for question in questions {
            let key = "\(question.identity.courseID)::\(question.identity.packID)"
            if queriedPacks.insert(key).inserted {
                seen.formUnion(progress.seenIdentities(courseID: question.identity.courseID, packID: question.identity.packID))
            }
        }
        return Set(questions.map(\.identity).filter { !seen.contains($0) })
    }

    func selectCourse(_ packKey: String) {
        guard catalog.select(packKey: packKey) else { return }
        // A session belongs to the old pack. Returning to Today is clearer
        // than carrying a numeric position into a newly selected course.
        activeSession = nil
        scheduledReviewStates = [:]
        selection = .none
        state = .today
    }

    func checkAnswer(_: Bool) {
        // Answering on tap can deliver a second selection change before the
        // Feedback screen replaces the question; only the first one counts.
        guard state == .question, let question = currentQuestion, var session = activeSession else { return }
        let correct = isCorrect(question)
        let answer = SessionAnswer(identity: question.identity, correct: correct, answeredAt: Date())
        session.answers.append(answer)
        activeSession = session
        progress.recordAndSave(answer)
        state = .feedback
    }

    func finishQuestion() {
        // Save while the answered item remains pinned. The pin prevents an async
        // save from swapping the feedback item before this transition completes.
        guard let session = activeSession else { return }
        progress.saveCurrentSession()
        advancePackResumePosition(for: session)
        selection = .none
        if let next = session.advanced() {
            activeSession = next
            state = .question
        } else {
            // Session exhausted — show the summary with the completed session snapshot.
            state = .results
        }
    }

    func skipQuestion() {
        // Skip records nothing. For .normal sessions the pack-level resume
        // position still advances past the skipped question so a relaunch does
        // not re-serve it. Then move to the next question or results.
        guard let session = activeSession else { return }
        advancePackResumePosition(for: session)
        selection = .none
        if let next = session.advanced() {
            activeSession = next
            state = .question
        } else {
            state = .results
        }
    }

    func endSession() {
        // When ending from the feedback screen the answer is already recorded.
        // Save it and advance the pack position so the resumption point is
        // consistent with having finished the question (C6).
        if case .feedback = state, let session = activeSession {
            progress.saveCurrentSession()
            advancePackResumePosition(for: session)
        }
        activeSession = nil
        selection = .none
        state = .today
    }

    /// Advances the pack-level resume position past the current session
    /// question so a relaunch starts at the next unreviewed question.
    /// Only written for `.normal` sessions; curated (SRS, retry) modes leave
    /// the position untouched because they serve a non-contiguous subset.
    private func advancePackResumePosition(for session: ActiveSession) {
        guard session.mode == .normal, let pack = catalog.pack else { return }
        let allQuestions = catalog.questions
        let questionCount = allQuestions.count
        guard questionCount > 0 else { return }
        // Compute the next index from the pack rather than the session so the
        // plan's starting offset is respected regardless of where it began.
        let currentPackIndex = allQuestions.firstIndex(where: {
            $0.identity == session.questions[session.position].identity
        }) ?? 0
        let nextPackIndex = (currentPackIndex + 1) % questionCount
        StudyResumePosition.store(
            nextPackIndex,
            courseID: pack.courseID,
            packID: pack.packID,
            questionCount: questionCount
        )
    }
}
