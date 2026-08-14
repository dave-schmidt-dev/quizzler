import SwiftUI
import QuizzlerKit

/// The six top-level states in Launchpad A.
enum LaunchpadState: String, CaseIterable, Identifiable {
    case today
    case question
    case feedback
    case results
    case progress
    case settings

    var id: String { rawValue }

    var title: String {
        rawValue.capitalized
    }

    var icon: String {
        switch self {
        case .today: "sun.max"
        case .question: "questionmark.circle"
        case .feedback: "checkmark.message"
        case .results: "chart.bar"
        case .progress: "chart.line.uptrend.xyaxis"
        case .settings: "gearshape"
        }
    }

    /// Launchpad keeps the study flow in one state machine while exposing
    /// only the three persistent destinations from the locked design.
    static let primaryNavigationStates: [LaunchpadState] = [.today, .progress, .settings]
}

/// A stable wrapper adds course/pack context to the typed question model.
struct SeededQuestion: Identifiable, Equatable, Sendable {
    let identity: QuestionIdentity
    let courseTitle: String
    let question: Question

    var id: String { identity.description }
    var qid: String { "\(identity.packID)::\(identity.questionID)" }
    var topic: String {
        switch question {
        case .multipleChoice(let value): value.metadata.topic
        case .scenarioMultipleChoice(let value): value.metadata.topic
        case .multipleSelect(let value): value.metadata.topic
        case .trueFalse(let value): value.metadata.topic
        case .matching(let value): value.metadata.topic
        }
    }
    var prompt: String {
        switch question {
        case .multipleChoice(let value): value.prompt
        case .scenarioMultipleChoice(let value): value.prompt
        case .multipleSelect(let value): value.prompt
        case .trueFalse(let value): value.prompt
        case .matching(let value): value.prompt
        }
    }
    var explanation: String {
        switch question {
        case .multipleChoice(let value): value.explanation
        case .scenarioMultipleChoice(let value): value.explanation
        case .multipleSelect(let value): value.explanation
        case .trueFalse(let value): value.explanation
        case .matching(let value): value.explanation
        }
    }
}

/// Small release-safe fixtures make a freshly installed app usable offline.
/// The true/false and matching entries are compatibility previews only; they
/// are not written into a new installable pack.
enum SeededStudyData {
    static let courseID = "security-plus"
    static let packID = "sy0-701"
    static let courseTitle = "Security+"
    static let metadata = QuestionMetadata(topic: "Threats and mitigations", examArea: "Architecture", difficulty: .medium)

    static let questions: [SeededQuestion] = [
        SeededQuestion(
            identity: QuestionIdentity(courseID: courseID, packID: packID, questionID: "q0042"),
            courseTitle: courseTitle,
            question: .multipleChoice(MultipleChoiceQuestion(
                id: "q0042", metadata: metadata,
                prompt: "Which control most directly limits lateral movement after an endpoint is compromised?",
                explanation: "Network segmentation limits which systems a compromised endpoint can reach.",
                options: ["Network segmentation", "Data masking", "Full-disk encryption", "Password rotation"], answer: 0
            ))
        ),
        SeededQuestion(
            identity: QuestionIdentity(courseID: courseID, packID: packID, questionID: "q0043"),
            courseTitle: courseTitle,
            question: .scenarioMultipleChoice(ScenarioMultipleChoiceQuestion(
                id: "q0043", metadata: metadata,
                prompt: "A team needs a second factor that resists phishing. Which choice is best?",
                explanation: "A hardware security key provides phishing-resistant authentication.",
                options: ["Hardware security key", "SMS code", "Security question", "Email link"], answer: 0
            ))
        ),
        SeededQuestion(
            identity: QuestionIdentity(courseID: courseID, packID: packID, questionID: "q0044"),
            courseTitle: courseTitle,
            question: .multipleSelect(MultipleSelectQuestion(
                id: "q0044", metadata: metadata,
                prompt: "Which two practices reduce the impact of exposed credentials?",
                explanation: "Least privilege and short credential lifetimes reduce what an exposed credential can do.",
                options: ["Least privilege", "Short credential lifetimes", "Shared admin accounts", "Permanent tokens"], answers: [0, 1]
            ))
        )
    ] + debugQuestions

#if DEBUG
    private static let debugQuestions: [SeededQuestion] = [
        SeededQuestion(
            identity: QuestionIdentity(courseID: courseID, packID: packID, questionID: "preview-true-false"),
            courseTitle: courseTitle,
            question: .trueFalse(TrueFalseQuestion(
                id: "preview-true-false", metadata: metadata,
                prompt: "Encryption by itself controls which internal hosts an attacker can reach.",
                explanation: "False. Segmentation, not encryption, constrains lateral movement.", answer: false
            ))
        ),
        SeededQuestion(
            identity: QuestionIdentity(courseID: courseID, packID: packID, questionID: "preview-matching"),
            courseTitle: courseTitle,
            question: .matching(MatchingQuestion(
                id: "preview-matching", metadata: metadata,
                prompt: "Match each control to its primary security goal.",
                explanation: "Each control is mapped to its primary goal in the preview data.",
                leftItems: ["Segmentation", "Encryption", "Least privilege"],
                rightItems: ["Limit reach", "Protect confidentiality", "Limit permissions"],
                correctPairs: [0, 1, 2]
            ))
        )
    ]
#else
    private static let debugQuestions: [SeededQuestion] = []
#endif

}

struct LaunchpadView: View {
    @State private var state: LaunchpadState = .today
    @State private var questionIndex = 0
    @State private var selection: QuestionSelection = .none
    @State private var answered = 0
    @State private var correct = 0
    @State private var sharedProgress = false

    private var currentQuestion: SeededQuestion {
        SeededStudyData.questions[questionIndex % SeededStudyData.questions.count]
    }

    var body: some View {
        VStack(spacing: 0) {
            consoleHeader
            content
            navigationBar
        }
        .preferredColorScheme(.dark)
        .background(QuizzlerTheme.terminalBackground.ignoresSafeArea())
    }

    private var consoleHeader: some View {
        HStack(alignment: .center, spacing: 10) {
            Text("Quizzler")
                .font(.title2.weight(.medium))
                .foregroundStyle(QuizzlerTheme.textPrimary)
            Text(syncStatus)
                .font(QuizzlerTheme.metadataFont)
                .foregroundStyle(QuizzlerTheme.textMuted)
                .lineLimit(1)
            Spacer(minLength: 0)
            Button {
                state = state == .settings ? .today : .settings
            } label: {
                Image(systemName: state == .settings ? "xmark" : "gearshape")
                    .font(.body.weight(.semibold))
                    .foregroundStyle(QuizzlerTheme.primaryCyan)
                    .frame(width: QuizzlerTheme.minimumTouchTarget, height: QuizzlerTheme.minimumTouchTarget)
            }
            .accessibilityLabel(state == .settings ? "Close settings" : "Open settings")
        }
        .padding(.horizontal, QuizzlerTheme.pageGutter)
        .padding(.top, 8)
        .padding(.bottom, 4)
        .background(QuizzlerTheme.terminalBackground)
    }

    private var syncStatus: String {
        switch state {
        case .today, .question, .progress: "shared · synced"
        case .feedback: "answer checked · saved"
        case .results: "session saved · synced"
        case .settings: "settings"
        }
    }

    @ViewBuilder private var content: some View {
        switch state {
        case .today:
            TodayView(onStart: startSession, onProgress: { state = .progress })
        case .question:
            QuestionShellView(
                seededQuestion: currentQuestion,
                phase: .question,
                selection: $selection,
                onCheck: checkAnswer,
                onFinish: {}
            )
        case .feedback:
            QuestionShellView(
                seededQuestion: currentQuestion,
                phase: .feedback(correct: isCurrentAnswerCorrect),
                selection: $selection,
                onCheck: { _ in },
                onFinish: finishQuestion
            )
        case .results:
            ResultsView(answered: answered, correct: correct, onNext: startSession, onProgress: { state = .progress })
        case .progress:
            ProgressView(answered: answered, correct: correct, sharedProgress: $sharedProgress)
        case .settings:
            SettingsView(sharedProgress: $sharedProgress)
        }
    }

    private var navigationBar: some View {
        HStack(spacing: 0) {
            ForEach(LaunchpadState.primaryNavigationStates) { destination in
                Button {
                    state = destination
                } label: {
                    Image(systemName: destination.icon)
                        .font(.body.weight(.semibold))
                        .foregroundStyle(selectedNavigationState == destination ? QuizzlerTheme.primaryCyan : QuizzlerTheme.textMuted)
                        .frame(maxWidth: .infinity, minHeight: QuizzlerTheme.minimumTouchTarget)
                }
                .accessibilityLabel(destination.title)
                .accessibilityValue(selectedNavigationState == destination ? "Selected" : "Not selected")
                .accessibilityAddTraits(selectedNavigationState == destination ? [.isSelected] : [])
            }
        }
        .padding(.horizontal, 4)
        .background(QuizzlerTheme.elevatedCard.opacity(0.75))
    }

    private var selectedNavigationState: LaunchpadState {
        switch state {
        case .question, .feedback, .results: .today
        default: state
        }
    }

    private var isCurrentAnswerCorrect: Bool {
        QuestionShellView.correctAnswer(for: currentQuestion.question, selection: selection)
    }

    private func startSession() {
        selection = .none
        state = .question
    }

    private func checkAnswer(_: Bool) {
        if isCurrentAnswerCorrect { correct += 1 }
        answered += 1
        state = .feedback
    }

    private func finishQuestion() {
        questionIndex = (questionIndex + 1) % SeededStudyData.questions.count
        state = .results
    }
}

private struct TodayView: View {
    let onStart: () -> Void
    let onProgress: () -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                eyebrow("Today · Security+")
                Text("A focused review, ready when you are.")
                    .font(.largeTitle.weight(.bold))
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                VStack(alignment: .leading, spacing: 14) {
                    HStack {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Continue review")
                                .font(.headline)
                            Text("Question 1 of 12")
                                .font(.subheadline)
                                .foregroundStyle(QuizzlerTheme.textMuted)
                        }
                        Spacer()
                        Text("3/12")
                            .font(.title2.monospacedDigit().weight(.semibold))
                            .foregroundStyle(QuizzlerTheme.primaryCyan)
                    }
                    Button("Start review", action: onStart)
                        .buttonStyle(.borderedProminent)
                        .tint(QuizzlerTheme.primaryCyan)
                        .foregroundStyle(.black)
                        .frame(maxWidth: .infinity, minHeight: 48)
                }
                .padding(18)
                .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
                Button("View progress", action: onProgress)
                    .buttonStyle(.bordered)
                    .tint(QuizzlerTheme.primaryCyan)
                    .frame(maxWidth: .infinity, minHeight: 44)
            }
            .padding(QuizzlerTheme.pageGutter)
        }
        .background(QuizzlerTheme.terminalBackground)
    }
}

private struct ResultsView: View {
    let answered: Int
    let correct: Int
    let onNext: () -> Void
    let onProgress: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            eyebrow("Results")
            Text("Session complete")
                .font(.largeTitle.weight(.bold))
                .foregroundStyle(QuizzlerTheme.textPrimary)
            Text("\(correct) correct · \(answered) answered")
                .font(.title3)
                .foregroundStyle(QuizzlerTheme.textPrimary)
            Button("Continue review", action: onNext)
                .buttonStyle(.borderedProminent)
                .tint(QuizzlerTheme.primaryCyan)
                .foregroundStyle(.black)
                .frame(maxWidth: .infinity, minHeight: 48)
            Button("View progress", action: onProgress)
                .buttonStyle(.bordered)
                .tint(QuizzlerTheme.primaryCyan)
                .frame(maxWidth: .infinity, minHeight: 44)
            Spacer()
        }
        .padding(QuizzlerTheme.pageGutter)
        .background(QuizzlerTheme.terminalBackground)
    }
}

private struct ProgressView: View {
    let answered: Int
    let correct: Int
    @Binding var sharedProgress: Bool

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                eyebrow("Progress")
                Text("Your study history")
                    .font(.largeTitle.weight(.bold))
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                stat("Answered", value: "\(answered)")
                stat("Correct", value: "\(correct)")
                Toggle("Shared progress", isOn: $sharedProgress)
                    .tint(QuizzlerTheme.primaryCyan)
                    .frame(minHeight: QuizzlerTheme.minimumTouchTarget)
                Text(sharedProgress ? "Shared progress is enabled for this device." : "Progress stays local on this device.")
                    .font(.subheadline)
                    .foregroundStyle(QuizzlerTheme.textMuted)
            }
            .padding(QuizzlerTheme.pageGutter)
        }
        .background(QuizzlerTheme.terminalBackground)
    }

    private func stat(_ label: String, value: String) -> some View {
        HStack {
            Text(label).foregroundStyle(QuizzlerTheme.textMuted)
            Spacer()
            Text(value).font(.title2.monospacedDigit()).foregroundStyle(QuizzlerTheme.primaryCyan)
        }
        .padding(16)
        .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
    }
}

private struct SettingsView: View {
    @Binding var sharedProgress: Bool

    var body: some View {
        Form {
            Section("Study") {
                Toggle("Shared progress", isOn: $sharedProgress)
                LabeledContent("Course", value: SeededStudyData.courseTitle)
                LabeledContent("App version", value: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1.0.0")
            }
            Section("About") {
                Text("Question packs stay on this device. Reports include question context only.")
            }
        }
        .scrollContentBackground(.hidden)
        .background(QuizzlerTheme.terminalBackground)
        .foregroundStyle(QuizzlerTheme.textPrimary)
    }
}

private func eyebrow(_ text: String) -> some View {
    Text(text.uppercased())
        .font(QuizzlerTheme.metadataFont)
        .foregroundStyle(QuizzlerTheme.primaryCyan)
}
