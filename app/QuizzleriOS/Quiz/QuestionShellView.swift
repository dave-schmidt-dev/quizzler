import SwiftUI
import QuizzlerKit

enum QuestionPhase: Equatable {
    case question
    case feedback(correct: Bool)
}

/// Shared shell for question and feedback states. The identity and issue action
/// live above the renderer so they stay reachable after an answer is checked.
struct QuestionShellView: View {
    let seededQuestion: SeededQuestion
    let phase: QuestionPhase
    @Binding var selection: QuestionSelection
    let onCheck: (Bool) -> Void
    let onFinish: () -> Void
    @State private var reportPresented = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack(alignment: .top, spacing: 12) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(seededQuestion.courseTitle.uppercased())
                            .font(QuizzlerTheme.metadataFont)
                            .foregroundStyle(QuizzlerTheme.primaryCyan)
                        Text(seededQuestion.topic)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(QuizzlerTheme.textMuted)
                    }
                    Spacer()
                    Button {
                        reportPresented = true
                    } label: {
                        Label("Report", systemImage: "exclamationmark.bubble")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(QuizzlerTheme.primaryCyan)
                            .frame(minWidth: QuizzlerTheme.minimumTouchTarget, minHeight: QuizzlerTheme.minimumTouchTarget)
                    }
                    .accessibilityHint("Report a problem with this question")
                    .accessibilityIdentifier("question-report")
                }

                HStack(alignment: .center) {
                    Text("qid: \(seededQuestion.qid)")
                        .font(QuizzlerTheme.metadataFont)
                        .foregroundStyle(QuizzlerTheme.textMuted)
                        .textSelection(.enabled)
                        .accessibilityLabel("Question ID \(seededQuestion.qid)")
                        .accessibilityIdentifier("question-qid")
                    Spacer()
                    Text(seededQuestion.question.type.rawValue.replacingOccurrences(of: "_", with: " "))
                        .font(QuizzlerTheme.metadataFont)
                        .foregroundStyle(QuizzlerTheme.textMuted)
                }

                Text(seededQuestion.prompt)
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)

                QuestionRenderer(question: seededQuestion.question, selection: $selection)
                    .disabled(isFeedback)

                if case .feedback(let correct) = phase {
                    FeedbackView(correct: correct, explanation: seededQuestion.explanation)
                }

                Button(action: primaryAction) {
                    Text(isFeedback ? "Finish Session" : "Check Answer")
                        .font(.headline)
                        .frame(maxWidth: .infinity, minHeight: 48)
                }
                .buttonStyle(.borderedProminent)
                .tint(QuizzlerTheme.primaryCyan)
                .foregroundStyle(.black)
                .disabled(!isFeedback && selection.isEmpty)
                .accessibilityHint(isFeedback ? "Return to your session results" : "Check the selected answer")
            }
            .padding(QuizzlerTheme.pageGutter)
        }
        .scrollBounceBehavior(.basedOnSize)
        .sheet(isPresented: $reportPresented) {
            ReportQuestionView(context: reportContext)
        }
        .background(QuizzlerTheme.terminalBackground.ignoresSafeArea())
        .accessibilityIdentifier(isFeedback ? "question-shell-feedback" : "question-shell")
    }

    private var isFeedback: Bool {
        if case .feedback = phase { return true }
        return false
    }

    private var reportContext: ReportQuestionContext {
        ReportQuestionContext(
            identity: seededQuestion.identity,
            qid: seededQuestion.qid,
            questionType: seededQuestion.question.type,
            course: seededQuestion.courseTitle,
            appVersion: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "unknown",
            build: Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "unknown",
            selectedResponse: responseSummary
        )
    }

    private var responseSummary: String {
        switch (seededQuestion.question, selection) {
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

    private func primaryAction() {
        switch phase {
        case .question:
            onCheck(isCorrect)
        case .feedback:
            onFinish()
        }
    }

    private var isCorrect: Bool {
        Self.correctAnswer(for: seededQuestion.question, selection: selection)
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
