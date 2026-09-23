import SwiftUI
import QuizzlerKit

enum QuestionPhase: Equatable {
    case question
    case feedback(correct: Bool)
}

/// Shared shell for question and feedback states. The identity and issue action
/// live above the renderer so they stay reachable after an answer is checked.
/// Where the learner is inside the current session, for the header counter.
/// `nil` outside a session, when there is no run of questions to count.
struct SessionPosition: Equatable {
    let index: Int
    let count: Int

    /// One-based, because "0 of 10" is not how anyone counts questions.
    var label: String { "\(index + 1) of \(count)" }

    /// Compact form shown in the header: "3/10".
    var compactLabel: String { "\(index + 1)/\(count)" }

    /// Progress through the session as a fraction in [0, 1].
    var fraction: Double { Double(index + 1) / Double(count) }
}

struct QuestionShellView: View {
    let studyQuestion: StudyQuestion
    let phase: QuestionPhase
    let sessionPosition: SessionPosition?
    let repository: any LaunchpadProgressRepository
    @Binding var selection: QuestionSelection
    let onCheck: (Bool) -> Void
    let onFinish: () -> Void
    var onSkip: () -> Void = {}
    var onEnd: () -> Void = {}
    @State private var reportPresented = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                headerRow

                // Topic caption, then the prompt.
                Text(studyQuestion.topicTitle)
                    .font(.subheadline)
                    .foregroundStyle(QuizzlerTheme.textMuted)

                Text(studyQuestion.prompt)
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)

                QuestionRenderer(question: studyQuestion.question, selection: $selection, revealCorrect: isFeedback)
                    .disabled(isFeedback)

                if case .feedback(let correct) = phase {
                    FeedbackView(correct: correct, explanation: studyQuestion.explanation)
                }

                // Non-tap-to-answer types show a "Check Answer" button in the question phase.
                if !isFeedback, !Self.answersOnTap(studyQuestion.question.type) {
                    Button {
                        onCheck(isCorrect)
                    } label: {
                        Text("Check Answer")
                            .font(.headline)
                            .frame(maxWidth: .infinity, minHeight: 48)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(QuizzlerTheme.primaryCyan)
                    .foregroundStyle(.black)
                    .disabled(selection.isEmpty)
                    .accessibilityHint("Check the selected answer")
                }
            }
            .padding(QuizzlerTheme.pageGutter)
            .padding(.bottom, QuizzlerTheme.scrollBottomInset)
        }
        .scrollBounceBehavior(.basedOnSize)
        // On the scroll view only: applied outside `safeAreaInset`, SwiftUI
        // stamps this identifier over the bottom bar's Report and Skip buttons.
        .accessibilityIdentifier(isFeedback ? "question-shell-feedback" : "question-shell")
        .safeAreaInset(edge: .bottom) {
            bottomBar
        }
        .sheet(isPresented: $reportPresented) {
            ReportQuestionView(context: reportContext, repository: repository)
        }
        .background(QuizzlerTheme.terminalBackground.ignoresSafeArea())
        .onChange(of: selection) { _, newSelection in
            // C4: single-answer types commit as soon as an option is tapped.
            guard !isFeedback, Self.answersOnTap(studyQuestion.question.type) else { return }
            guard !newSelection.isEmpty else { return }
            onCheck(isCorrect)
        }
    }

    // MARK: - Header

    private var headerRow: some View {
        HStack(spacing: 12) {
            // X button — ends the session.
            Button {
                onEnd()
            } label: {
                Image(systemName: "xmark")
                    .font(.body.weight(.semibold))
                    .foregroundStyle(QuizzlerTheme.textMuted)
                    .frame(width: QuizzlerTheme.minimumTouchTarget, height: QuizzlerTheme.minimumTouchTarget)
            }
            .accessibilityLabel("End session")
            .accessibilityIdentifier("session-end")

            if let sessionPosition {
                // Thin progress bar filling the available middle space.
                ProgressView(value: sessionPosition.fraction)
                    .progressViewStyle(.linear)
                    .tint(QuizzlerTheme.primaryCyan)
                    .frame(maxWidth: .infinity)

                // Compact counter, e.g. "3/10".
                Text(sessionPosition.compactLabel)
                    .font(QuizzlerTheme.metadataFont.monospacedDigit())
                    .foregroundStyle(QuizzlerTheme.textMuted)
                    .accessibilityLabel("Question \(sessionPosition.label) in this session")
                    .accessibilityIdentifier("session-position")
            }
        }
    }

    // MARK: - Bottom bar (pinned to safe area bottom)

    @ViewBuilder private var bottomBar: some View {
        if isFeedback {
            feedbackBottomBar
        } else {
            questionBottomBar
        }
    }

    private var questionBottomBar: some View {
        HStack(spacing: 12) {
            // Report flag — left side.
            Button {
                reportPresented = true
            } label: {
                Image(systemName: "flag")
                    .font(.body)
                    .foregroundStyle(QuizzlerTheme.textMuted)
                    .frame(width: QuizzlerTheme.minimumTouchTarget, height: QuizzlerTheme.minimumTouchTarget)
            }
            .accessibilityLabel("Report")
            .accessibilityValue("Question ID \(studyQuestion.qid)")
            .accessibilityIdentifier("question-report")

            Spacer()

            // Skip — right side.
            Button {
                onSkip()
            } label: {
                Label("Skip", systemImage: "arrow.right")
                    .font(.body.weight(.semibold))
                    .foregroundStyle(QuizzlerTheme.textMuted)
                    .frame(minHeight: QuizzlerTheme.minimumTouchTarget)
            }
            .accessibilityLabel("Skip")
            .accessibilityIdentifier("question-skip")
        }
        .padding(.horizontal, QuizzlerTheme.pageGutter)
        .padding(.vertical, 10)
        .background(QuizzlerTheme.terminalBackground)
    }

    private var feedbackBottomBar: some View {
        HStack(spacing: 12) {
            // Square 48 pt flag button — report.
            Button {
                reportPresented = true
            } label: {
                Image(systemName: "flag")
                    .font(.body)
                    .foregroundStyle(QuizzlerTheme.textMuted)
                    .frame(width: 48, height: 48)
                    .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
            }
            .accessibilityLabel("Report")
            .accessibilityValue("Question ID \(studyQuestion.qid)")
            .accessibilityIdentifier("question-report")

            // Full-width "Next question" button.
            Button {
                onFinish()
            } label: {
                Text("Next question")
                    .font(.headline)
                    .frame(maxWidth: .infinity, minHeight: 48)
            }
            .buttonStyle(.borderedProminent)
            .tint(QuizzlerTheme.primaryCyan)
            .foregroundStyle(.black)
            .accessibilityHint("Continue to the next question")
        }
        .padding(.horizontal, QuizzlerTheme.pageGutter)
        .padding(.vertical, 10)
        .background(QuizzlerTheme.terminalBackground)
    }

    // MARK: - Helpers

    private var isFeedback: Bool {
        if case .feedback = phase { return true }
        return false
    }

    private var reportContext: ReportQuestionContext {
        ReportQuestionContext(
            identity: studyQuestion.identity,
            qid: studyQuestion.qid,
            questionType: studyQuestion.question.type,
            appVersion: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "unknown",
            build: Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "unknown",
            selectedResponse: responseSummary,
            prompt: studyQuestion.prompt,
            options: Self.reportOptions(for: studyQuestion.question)
        )
    }

    /// The answer-option strings passed to the report sheet for every question type.
    static func reportOptions(for question: Question) -> [String] {
        switch question {
        case .multipleChoice(let q):         return q.options
        case .scenarioMultipleChoice(let q): return q.options
        case .multipleSelect(let q):         return q.options
        case .trueFalse:                     return ["True", "False"]
        case .matching:                      return []
        }
    }

    /// Whether this question type should commit an answer immediately on tap
    /// rather than waiting for an explicit "Check Answer" button press.
    static func answersOnTap(_ type: QuestionType) -> Bool {
        switch type {
        case .multipleChoice, .scenarioMultipleChoice, .trueFalse: true
        case .multipleSelect, .matching:                           false
        }
    }

    private var responseSummary: String {
        switch (studyQuestion.question, selection) {
        case (.multipleChoice(let question), .single(let index)):
            return question.options.indices.contains(index) ? question.options[index] : "None"
        case (.scenarioMultipleChoice(let question), .single(let index)):
            return question.options.indices.contains(index) ? question.options[index] : "None"
        case (.multipleSelect(let question), .multiple(let indexes)):
            return indexes.sorted().compactMap { question.options.indices.contains($0) ? question.options[$0] : nil }.joined(separator: ", ")
        case (.trueFalse, .boolean(let value)):
            return value ? "True" : "False"
        case (.matching(let question), .matching(let indexes)):
            return indexes.enumerated().compactMap { index, right in
                guard question.leftItems.indices.contains(index), question.rightItems.indices.contains(right) else { return nil }
                return "\(question.leftItems[index]) → \(question.rightItems[right])"
            }.joined(separator: ", ")
        default:
            return "None"
        }
    }

    private var isCorrect: Bool {
        Self.correctAnswer(for: studyQuestion.question, selection: selection)
    }

    static func correctAnswer(for question: Question, selection: QuestionSelection) -> Bool {
        switch (question, selection) {
        case (.multipleChoice(let question), .single(let answer)):
            return answer == question.answer
        case (.scenarioMultipleChoice(let question), .single(let answer)):
            return answer == question.answer
        case (.multipleSelect(let question), .multiple(let answers)):
            return answers == Set(question.answers)
        case (.trueFalse(let question), .boolean(let answer)):
            return answer == question.answer
        case (.matching(let question), .matching(let answers)):
            return answers == question.correctPairs
        default:
            return false
        }
    }
}

private struct FeedbackView: View {
    let correct: Bool
    let explanation: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(correct ? "Correct" : "Review this answer", systemImage: correct ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
                .font(.headline)
                .foregroundStyle(correct ? QuizzlerTheme.success : QuizzlerTheme.warning)
            Text(explanation)
                .font(QuizzlerTheme.readableFont)
                .foregroundStyle(QuizzlerTheme.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
        .accessibilityElement(children: .combine)
        .accessibilityLabel(correct ? "Correct. \(explanation)" : "Review this answer. \(explanation)")
    }
}
