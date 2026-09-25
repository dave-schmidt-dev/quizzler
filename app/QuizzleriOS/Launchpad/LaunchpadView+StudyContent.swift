import Foundation
import SwiftUI
import QuizzlerKit
import UIKit

extension LaunchpadView {
    @ViewBuilder var studyContent: some View {
        if !progress.isReadyForStudy {
            StudyPreparationView(state: progress.persistenceState)
        } else {
            switch catalog.state {
            case .loading:
                PackLoadingView()
            case .unavailable(let reason):
                NoPackInstalledView(reason: reason, onProgress: { state = .progress })
            case .ready(let pack, let questions):
                if let question = currentQuestion {
                    readyContent(pack: pack, questions: questions, question: question)
                } else {
                    NoPackInstalledView(reason: "The installed pack contains no questions.", onProgress: { state = .progress })
                }
            }
        }
    }

    @ViewBuilder private func readyContent(pack: InstalledPack, questions: [StudyQuestion], question: StudyQuestion) -> some View {
        let seenCount = progress.seenIdentities(courseID: pack.courseID, packID: pack.packID).count
        let unseenCount = max(0, questions.count - seenCount)
        switch state {
        case .today:
            TodayView(
                questionNumber: resumeIndex(count: questions.count) + 1,
                questionCount: questions.count,
                unseenCount: unseenCount,
                dueCount: currentInsights.due.due,
                scheduledReviewEnabled: scheduledReviewEnabled,
                sessionLength: effectiveSessionLength,
                missedCount: currentInsights.recentMisses.count,
                maximumLeitnerLevel: progress.maximumLeitnerLevel,
                onStart: startSession,
                onStartDueReview: startDueReview,
                onStartRetryMissed: startRetryMissed,
                onChooseNextSessionLength: { nextSessionLengthOverride = $0 }
            )
        case .question:
            QuestionShellView(
                studyQuestion: question,
                phase: .question,
                repository: repository,
                progressEnvelope: progress.envelope,
                maximumLeitnerLevel: progress.maximumLeitnerLevel,
                sessionMode: activeSession?.mode ?? .normal,
                reviewStateAtSessionStart: scheduledReviewStates[question.identity],
                answerTimestamp: activeSession?.answers.last(where: { $0.identity == question.identity })?.answeredAt,
                selection: $selection,
                onCheck: checkAnswer,
                onFinish: {},
                onSkip: skipQuestion
            )
        case .feedback:
            QuestionShellView(
                studyQuestion: question,
                phase: .feedback(correct: isCorrect(question)),
                repository: repository,
                progressEnvelope: progress.envelope,
                maximumLeitnerLevel: progress.maximumLeitnerLevel,
                sessionMode: activeSession?.mode ?? .normal,
                reviewStateAtSessionStart: scheduledReviewStates[question.identity],
                answerTimestamp: activeSession?.answers.last(where: { $0.identity == question.identity })?.answeredAt,
                selection: $selection,
                onCheck: { _ in },
                onFinish: finishQuestion,
                onSkip: skipQuestion
            )
        case .results:
            if let session = activeSession {
                SessionSummaryView(
                    session: session,
                    courseTitle: pack.subject,
                    saving: progress.persistenceState == .saving,
                    saveFailed: progress.persistenceState == .saveFailed,
                    syncPending: progress.persistenceState == .syncPending,
                    accountChanged: progress.persistenceState == .accountChanged,
                    onRetrySave: progress.saveCurrentSession,
                    onRetryMissed: startRetryMissedFromSession,
                    onNext: startSession,
                    onDone: { state = .today }
                )
            } else {
                // No session snapshot means the state machine reached .results
                // without completing a plan — fall back to Today rather than a
                // blank screen, which would look like a crash to the learner.
                TodayView(
                    questionNumber: resumeIndex(count: questions.count) + 1,
                    questionCount: questions.count,
                    unseenCount: unseenCount,
                    dueCount: currentInsights.due.due,
                    scheduledReviewEnabled: scheduledReviewEnabled,
                    sessionLength: effectiveSessionLength,
                    missedCount: currentInsights.recentMisses.count,
                    maximumLeitnerLevel: progress.maximumLeitnerLevel,
                    onStart: startSession,
                    onStartDueReview: startDueReview,
                    onStartRetryMissed: startRetryMissed,
                    onChooseNextSessionLength: { nextSessionLengthOverride = $0 }
                )
            }
        case .progress, .settings:
            EmptyView()
        }
    }

    /// `nil` when no session is running, so a resumed single question is not
    /// captioned with a run length it is not part of.
    var sessionPosition: SessionPosition? {
        guard let session = activeSession, session.position < session.questions.count else { return nil }
        return SessionPosition(index: session.position, count: session.questions.count)
    }

    func isCorrect(_ question: StudyQuestion) -> Bool {
        QuestionShellView.correctAnswer(for: question.question, selection: selection)
    }
}

/// Shown while the bundled packs are being decoded (INV-1: the wait is visible).
private struct PackLoadingView: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            eyebrow("Today")
            Label("Loading question packs…", systemImage: "arrow.triangle.2.circlepath")
                .font(.headline)
                .foregroundStyle(QuizzlerTheme.textMuted)
                .accessibilityLabel("Loading question packs")
            Spacer()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(QuizzlerTheme.pageGutter)
        .background(QuizzlerTheme.terminalBackground)
        .accessibilityIdentifier("pack-loading")
    }
}

/// The honest empty state.
///
/// This build carries no questions of its own, so when nothing is installed
/// there is nothing to study and the screen says exactly that. Substituting
/// built-in sample questions here would make an empty install look like a
/// working course, which is the defect this screen replaced.
private struct NoPackInstalledView: View {
    let reason: String
    let onProgress: () -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                eyebrow("Today")
                Text("No questions available")
                    .font(.largeTitle.weight(.bold))
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                Text(reason)
                    .font(.subheadline)
                    .foregroundStyle(QuizzlerTheme.danger)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("no-pack-reason")
                Text("Question packs are added when the app is built. Install a pack and build again.")
                    .font(.subheadline)
                    .foregroundStyle(QuizzlerTheme.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
                Button("View progress", action: onProgress)
                    .buttonStyle(.bordered)
                    .tint(QuizzlerTheme.primaryCyan)
                    .frame(maxWidth: .infinity, minHeight: 44)
            }
            .padding(QuizzlerTheme.pageGutter)
            .padding(.bottom, QuizzlerTheme.scrollBottomInset)
        }
        .background(QuizzlerTheme.terminalBackground)
        .accessibilityIdentifier("no-pack-installed")
    }
}

private struct StudyPreparationView: View {
    let state: LaunchpadProgressModel.PersistenceState

    private var canRetry: Bool {
        state == .syncPending || state == .saveFailed
    }

    var body: some View {
        VStack(spacing: 14) {
            if canRetry {
                Image(systemName: "exclamationmark.icloud")
                    .font(.largeTitle)
                    .foregroundStyle(QuizzlerTheme.warning)
                Text("Progress setup needs attention")
                    .font(.headline)
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                Text("Scheduled review settings must finish syncing before study can start. Tap the status badge above to retry.")
                    .font(.subheadline)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(QuizzlerTheme.textMuted)
            } else if state == .accountChanged {
                Image(systemName: "person.crop.circle.badge.exclamationmark")
                    .font(.largeTitle)
                    .foregroundStyle(QuizzlerTheme.warning)
                Text("iCloud account changed")
                    .font(.headline)
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                Text("Return to the account that owns this progress to finish preparing reviews.")
                    .font(.subheadline)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(QuizzlerTheme.textMuted)
            } else {
                ProgressView()
                    .tint(QuizzlerTheme.primaryCyan)
                Text("Preparing your reviews…")
                    .font(.subheadline)
                    .foregroundStyle(QuizzlerTheme.textMuted)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(QuizzlerTheme.pageGutter)
        .background(QuizzlerTheme.terminalBackground)
        .accessibilityIdentifier("study-preparation")
    }
}
