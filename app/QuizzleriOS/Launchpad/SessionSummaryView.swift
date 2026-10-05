import SwiftUI
import QuizzlerKit

/// One missed question with everything its read-only review needs: the full
/// prompt, the option text the learner should have chosen, and the explanation.
struct MissedQuestionDetail: Equatable, Identifiable {
    let identity: QuestionIdentity
    let prompt: String
    let correctAnswer: String
    let explanation: String

    init(question: StudyQuestion) {
        self.identity = question.identity
        self.prompt = question.prompt
        self.correctAnswer = Self.correctAnswerText(for: question.question)
        self.explanation = question.explanation
    }

    var id: QuestionIdentity { identity }

    /// The option text the learner should have chosen, in row order.
    static func correctAnswerText(for question: Question) -> String {
        switch question {
        case .multipleChoice(let question):         return option(question.answer, in: question.options)
        case .scenarioMultipleChoice(let question): return option(question.answer, in: question.options)
        case .multipleSelect(let question):
            return question.answers.sorted()
                .compactMap { question.options.indices.contains($0) ? question.options[$0] : nil }
                .joined(separator: ", ")
        }
    }

    /// A bad pack index reads as empty rather than crashing the summary.
    private static func option(_ index: Int, in options: [String]) -> String {
        options.indices.contains(index) ? options[index] : ""
    }
}

/// Pure metrics distilled from an active session.
struct SessionSummary: Equatable {
    let right: Int
    let answered: Int
    let newLearned: Int
    let toRetry: Int
    let missedPrompts: [String]
    let missedDetails: [MissedQuestionDetail]

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
            session.questions.map { ($0.identity, $0) },
            uniquingKeysWith: { first, _ in first }
        )
        let details = distinctWrong.compactMap { questionMap[$0] }.map(MissedQuestionDetail.init)
        self.missedDetails = details
        self.missedPrompts = details.map(\.prompt)
    }
}

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
    @State private var selectedMissedDetail: MissedQuestionDetail?

    private var summary: SessionSummary {
        SessionSummary(session: session)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("\(summary.right) of \(summary.answered) right")
                    .font(.largeTitle.weight(.bold))
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                    .accessibilityIdentifier("session-complete-heading")
                    .accessibilityAddTraits(.isHeader)

                statCards

                if !summary.missedDetails.isEmpty {
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
        .sheet(item: $selectedMissedDetail) { detail in
            MissedQuestionDetailView(detail: detail)
        }
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
                ForEach(Array(summary.missedDetails.enumerated()), id: \.element.identity) { index, detail in
                    if index > 0 {
                        Divider().background(QuizzlerTheme.border)
                    }
                    // The row stays one line; the full prompt, the correct
                    // answer and the explanation are one tap away (C6).
                    Button {
                        selectedMissedDetail = detail
                    } label: {
                        HStack(spacing: 8) {
                            Text(detail.prompt)
                                .font(.subheadline)
                                .foregroundStyle(QuizzlerTheme.textPrimary)
                                .lineLimit(1)
                                .truncationMode(.tail)
                            Spacer()
                            Image(systemName: "chevron.right")
                                .font(.caption)
                                .foregroundStyle(QuizzlerTheme.textMuted)
                        }
                        .padding(.horizontal, 16)
                        .padding(.vertical, 12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(detail.prompt)
                    .accessibilityHint("Shows the correct answer and explanation")
                    .accessibilityIdentifier("session-missed-row-\(index)")
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
                // Retry keeps the primary slot, but continuing no longer
                // needs the Done → Today → Start detour (C6).
                HStack(spacing: 12) {
                    retryMissedButton
                    nextSessionButton(prominent: false)
                }
            } else {
                nextSessionButton(prominent: true)
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

    private var retryMissedButton: some View {
        Button(action: onRetryMissed) {
            Text("Retry the \(summary.toRetry) missed")
                .font(.headline)
                .frame(maxWidth: .infinity, minHeight: 48)
        }
        .buttonStyle(.borderedProminent)
        .tint(QuizzlerTheme.primaryCyan)
        .foregroundStyle(.black)
        .keyboardShortcut(.return, modifiers: [])
        .accessibilityLabel("Retry the \(summary.toRetry) missed")
        .accessibilityIdentifier("session-retry-missed")
    }

    /// Next session is one tap whether or not anything was missed: primary
    /// when the session was clean, secondary beside Retry otherwise.
    @ViewBuilder private func nextSessionButton(prominent: Bool) -> some View {
        if prominent {
            Button(action: onNext) {
                nextSessionLabel
            }
            .buttonStyle(.borderedProminent)
            .tint(QuizzlerTheme.primaryCyan)
            .foregroundStyle(.black)
            .keyboardShortcut(.return, modifiers: [])
            .accessibilityLabel("Continue to next session")
        } else {
            Button(action: onNext) {
                nextSessionLabel
            }
            .buttonStyle(.bordered)
            .tint(QuizzlerTheme.primaryCyan)
            .accessibilityLabel("Continue to next session")
        }
    }

    private var nextSessionLabel: some View {
        Text("Next session")
            .font(.headline)
            .frame(maxWidth: .infinity, minHeight: 48)
    }
}

/// Read-only review of one missed question, opened from the summary's missed
/// row. Everything it shows comes from the session's own questions, so the
/// summary needs nothing beyond the data it already holds.
private struct MissedQuestionDetailView: View {
    let detail: MissedQuestionDetail
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text(detail.prompt)
                        .font(.title3.weight(.semibold))
                        .foregroundStyle(QuizzlerTheme.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityAddTraits(.isHeader)
                        .accessibilityIdentifier("missed-detail-prompt")

                    VStack(alignment: .leading, spacing: 8) {
                        Text("Correct answer")
                            .font(QuizzlerTheme.metadataFont)
                            .foregroundStyle(QuizzlerTheme.textMuted)
                        Text(detail.correctAnswer)
                            .font(.body.weight(.semibold))
                            .foregroundStyle(QuizzlerTheme.success)
                            .fixedSize(horizontal: false, vertical: true)
                            .accessibilityIdentifier("missed-detail-correct-answer")
                    }
                    .padding(16)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))

                    Text(detail.explanation)
                        .font(QuizzlerTheme.readableFont)
                        .foregroundStyle(QuizzlerTheme.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("missed-detail-explanation")
                }
                .padding(QuizzlerTheme.pageGutter)
                .padding(.bottom, QuizzlerTheme.stackGap)
            }
            .background(QuizzlerTheme.terminalBackground)
            .navigationTitle("Missed question")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") {
                        dismiss()
                    }
                    .accessibilityIdentifier("missed-detail-done")
                }
            }
        }
    }
}
