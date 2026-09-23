import SwiftUI
import QuizzlerKit

/// Pure metrics distilled from an active session.
struct SessionSummary: Equatable {
    let right: Int
    let answered: Int
    let newLearned: Int
    let toRetry: Int
    let missedPrompts: [String]

    init(session: ActiveSession) {
        self.answered = session.answers.count
        self.right = session.answers.filter(\.correct).count

        var learnedIdentities = Set<QuestionIdentity>()
        for answer in session.answers where answer.correct && session.newIdentities.contains(answer.identity) {
            learnedIdentities.insert(answer.identity)
        }
        self.newLearned = learnedIdentities.count

        var seenWrong = Set<QuestionIdentity>()
        var distinctWrong: [QuestionIdentity] = []
        for answer in session.answers where !answer.correct {
            if seenWrong.insert(answer.identity).inserted {
                distinctWrong.append(answer.identity)
            }
        }
        self.toRetry = distinctWrong.count

        let questionMap = Dictionary(
            session.questions.map { ($0.identity, $0.prompt) },
            uniquingKeysWith: { first, _ in first }
        )
        self.missedPrompts = distinctWrong.compactMap { questionMap[$0] }
    }
}

/// Session summary shown when the last question of a plan is answered.
///
/// Scores come from the session's own answer list, not the lifetime aggregate,
/// so the numbers match exactly what the learner just completed.
struct SessionSummaryView: View {
    let session: ActiveSession
    let courseTitle: String
    let saving: Bool
    let saveFailed: Bool
    let syncPending: Bool
    let accountChanged: Bool
    let onRetrySave: () -> Void
    let onRetryMissed: () -> Void
    let onNext: () -> Void
    let onDone: () -> Void

    private var summary: SessionSummary {
        SessionSummary(session: session)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("\(courseTitle) · Session done")
                    .font(.subheadline)
                    .foregroundStyle(QuizzlerTheme.textMuted)

                Text("\(summary.right) of \(summary.answered) right")
                    .font(.largeTitle.weight(.bold))
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                    .accessibilityIdentifier("session-complete-heading")
                    .accessibilityAddTraits(.isHeader)

                statCards

                if !summary.missedPrompts.isEmpty {
                    missedSection
                }

                persistenceStatusRows
            }
            .padding(QuizzlerTheme.pageGutter)
            .padding(.bottom, QuizzlerTheme.stackGap)
        }
        .safeAreaInset(edge: .bottom) {
            actionButtons
                .padding(.horizontal, QuizzlerTheme.pageGutter)
                .padding(.vertical, 12)
                .background(QuizzlerTheme.terminalBackground)
        }
        .background(QuizzlerTheme.terminalBackground)
        .accessibilityIdentifier("session-summary")
    }

    // MARK: - Subviews

    private var statCards: some View {
        HStack(spacing: 12) {
            statCard(value: summary.newLearned, label: "new learned")
            statCard(value: summary.toRetry, label: "to retry")
        }
    }

    private func statCard(value: Int, label: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("\(value)")
                .font(.title2.monospacedDigit().weight(.bold))
                .foregroundStyle(QuizzlerTheme.textPrimary)
            Text(label)
                .font(.caption)
                .foregroundStyle(QuizzlerTheme.textMuted)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(value) \(label)")
    }

    private var missedSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Missed")
                .font(.subheadline)
                .foregroundStyle(QuizzlerTheme.textMuted)

            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(summary.missedPrompts.enumerated()), id: \.offset) { index, prompt in
                    if index > 0 {
                        Divider().background(QuizzlerTheme.border)
                    }
                    Text(prompt)
                        .font(.subheadline)
                        .foregroundStyle(QuizzlerTheme.textPrimary)
                        .lineLimit(1)
                        .truncationMode(.tail)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
        }
    }

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

    private var actionButtons: some View {
        VStack(spacing: 12) {
            if summary.toRetry > 0 {
                Button(action: onRetryMissed) {
                    Text("Retry the \(summary.toRetry) missed")
                        .font(.headline)
                        .frame(maxWidth: .infinity, minHeight: 48)
                }
                .buttonStyle(.borderedProminent)
                .tint(QuizzlerTheme.primaryCyan)
                .foregroundStyle(.black)
                .accessibilityLabel("Retry the \(summary.toRetry) missed")
                .accessibilityIdentifier("session-retry-missed")
            } else {
                Button(action: onNext) {
                    Text("Next session")
                        .font(.headline)
                        .frame(maxWidth: .infinity, minHeight: 48)
                }
                .buttonStyle(.borderedProminent)
                .tint(QuizzlerTheme.primaryCyan)
                .foregroundStyle(.black)
                .accessibilityLabel("Continue to next session")
            }

            Button(action: onDone) {
                Text("Done")
                    .font(.headline)
                    .frame(maxWidth: .infinity, minHeight: 44)
            }
            .buttonStyle(.bordered)
            .tint(QuizzlerTheme.primaryCyan)
            .accessibilityLabel("Return to Today")
        }
    }
}
