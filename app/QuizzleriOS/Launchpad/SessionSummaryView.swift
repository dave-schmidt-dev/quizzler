import SwiftUI
import QuizzlerKit

/// Session summary shown when the last question of a plan is answered.
///
/// Scores come from the session's own answer list, not the lifetime aggregate,
/// so the numbers match exactly what the learner just completed.
struct SessionSummaryView: View {
    let session: ActiveSession
    let saving: Bool
    let saveFailed: Bool
    let syncPending: Bool
    let accountChanged: Bool
    let onRetrySave: () -> Void
    let onRetryMissed: () -> Void
    let onNext: () -> Void
    let onDone: () -> Void

    private var correct: Int { session.answers.filter(\.correct).count }
    private var answered: Int { session.answers.count }

    /// Wrong answers in the order they were answered, deduplicated by identity
    /// so a question retried within the session only appears once.
    private var missed: [SessionAnswer] {
        var seen = Set<QuestionIdentity>()
        return session.answers.filter { answer in
            !answer.correct && seen.insert(answer.identity).inserted
        }
    }

    private var missedQuestions: [(answer: SessionAnswer, question: StudyQuestion?)] {
        missed.map { answer in
            let sq = session.questions.first { $0.identity == answer.identity }
            return (answer, sq)
        }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                eyebrow("Results")

                // UI-test asserts this exact string — do not change the copy.
                Text("Session complete")
                    .font(.largeTitle.weight(.bold))
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                    .accessibilityIdentifier("session-complete-heading")

                Text("\(correct) correct · \(answered) answered")
                    .font(.title3)
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                    .accessibilityLabel("\(correct) correct of \(answered) answered")

                persistenceStatusRows

                if !missedQuestions.isEmpty {
                    missedSection
                }

                actionButtons
            }
            .padding(QuizzlerTheme.pageGutter)
            .padding(.bottom, QuizzlerTheme.scrollBottomInset)
        }
        .background(QuizzlerTheme.terminalBackground)
        .accessibilityIdentifier("session-summary")
    }

    // MARK: - Subviews

    @ViewBuilder private var persistenceStatusRows: some View {
        if saving {
            Label("Saving progress locally…", systemImage: "arrow.triangle.2.circlepath")
                .foregroundStyle(QuizzlerTheme.textMuted)
                .accessibilityLabel("Saving progress locally")
        } else if accountChanged {
            Text("This device has a different iCloud account. Your study history is safe on this device. Sign in to the original account to resume syncing.")
                .foregroundStyle(QuizzlerTheme.textMuted)
        } else if syncPending {
            Text("Progress is saved on this device. iCloud has not updated yet.")
                .foregroundStyle(QuizzlerTheme.textMuted)
            Button("Retry sync", action: onRetrySave)
                .buttonStyle(.bordered)
                .tint(QuizzlerTheme.primaryCyan)
                .frame(minHeight: 44)
                .accessibilityLabel("Retry iCloud sync")
        } else if saveFailed {
            Text("Progress was not saved. Retry before continuing.")
                .foregroundStyle(QuizzlerTheme.danger)
            Button("Retry save", action: onRetrySave)
                .buttonStyle(.bordered)
                .tint(QuizzlerTheme.primaryCyan)
                .frame(minHeight: 44)
                .accessibilityLabel("Retry saving progress")
        }
    }

    private var missedSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Missed")
                .font(.headline)
                .foregroundStyle(QuizzlerTheme.textPrimary)

            ForEach(missedQuestions, id: \.answer.identity) { entry in
                VStack(alignment: .leading, spacing: 4) {
                    // Truncate long prompts so the list stays scannable.
                    Text(entry.question?.prompt ?? "Unknown question")
                        .font(.subheadline)
                        .foregroundStyle(QuizzlerTheme.textPrimary)
                        .lineLimit(2)
                    Text(entry.question?.qid ?? entry.answer.identity.shortLabel)
                        .font(QuizzlerTheme.metadataFont)
                        .foregroundStyle(QuizzlerTheme.textMuted)
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
            }
        }
    }

    private var actionButtons: some View {
        VStack(spacing: 12) {
            Button(action: onRetryMissed) {
                Text("Retry missed").frame(maxWidth: .infinity, minHeight: 48)
            }
                .buttonStyle(.borderedProminent)
                .tint(QuizzlerTheme.danger)
                .foregroundStyle(.white)
                // Disabled when the session was perfect — no wrong answers to retry.
                .disabled(missed.isEmpty)
                .accessibilityLabel("Retry missed questions")
                .accessibilityHint(missed.isEmpty ? "No missed questions this session" : "Starts a new session with \(missed.count) missed question\(missed.count == 1 ? "" : "s")")

            Button(action: onNext) {
                Text("Continue").frame(maxWidth: .infinity, minHeight: 48)
            }
                .buttonStyle(.borderedProminent)
                .tint(QuizzlerTheme.primaryCyan)
                .foregroundStyle(.black)
                .accessibilityLabel("Continue to next session")

            Button(action: onDone) {
                Text("Done").frame(maxWidth: .infinity, minHeight: 44)
            }
                .buttonStyle(.bordered)
                .tint(QuizzlerTheme.primaryCyan)
                .accessibilityLabel("Return to Today")
        }
    }
}

// MARK: - Helpers

private extension QuestionIdentity {
    /// Pack-scoped short label displayed in monospace alongside the missed prompt.
    var shortLabel: String { "\(packID)::\(questionID)" }
}
