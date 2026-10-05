import SwiftUI
import QuizzlerKit

enum QuestionPhase: Equatable {
    case question
    case feedback(correct: Bool)
}

/// Where the learner is inside the current session, for the persistent header.
struct SessionPosition: Equatable {
    let index: Int
    let count: Int

    /// One-based, because "0 of 10" is not how anyone counts questions.
    var label: String { "\(index + 1) of \(count)" }

    /// Visible counter: "Question 1 of 10".
    var displayLabel: String { "Question \(label)" }

    /// Visible counter in the accepted compact form: "1/10".
    var counterLabel: String { "\(index + 1)/\(count)" }

    /// Progress through the session as a fraction in [0, 1].
    var fraction: Double { Double(index + 1) / Double(count) }
}

/// Shared shell for question and feedback states. The issue action stays
/// reachable after an answer is checked.
struct QuestionShellView: View {
    let studyQuestion: StudyQuestion
    let phase: QuestionPhase
    let repository: any LaunchpadProgressRepository
    let progressEnvelope: ProgressEnvelope?
    let maximumLeitnerLevel: Int
    let sessionMode: SelectionMode
    let reviewStateAtSessionStart: SRSState?
    let answerTimestamp: Date?
    @Binding var selection: QuestionSelection
    let onCheck: (Bool) -> Void
    let onFinish: () -> Void
    var onSkip: () -> Void = {}
    @State private var reportPresented = false
    @State private var historyPresented = false
    @State private var whyPresented = false

    init(
        studyQuestion: StudyQuestion,
        phase: QuestionPhase,
        repository: any LaunchpadProgressRepository,
        progressEnvelope: ProgressEnvelope? = nil,
        maximumLeitnerLevel: Int = LeitnerSchedule.defaultMaximumLevel,
        sessionMode: SelectionMode = .normal,
        reviewStateAtSessionStart: SRSState? = nil,
        answerTimestamp: Date? = nil,
        selection: Binding<QuestionSelection>,
        onCheck: @escaping (Bool) -> Void,
        onFinish: @escaping () -> Void,
        onSkip: @escaping () -> Void = {}
    ) {
        self.studyQuestion = studyQuestion
        self.phase = phase
        self.repository = repository
        self.progressEnvelope = progressEnvelope
        self.maximumLeitnerLevel = maximumLeitnerLevel
        self.sessionMode = sessionMode
        self.reviewStateAtSessionStart = reviewStateAtSessionStart
        self.answerTimestamp = answerTimestamp
        self._selection = selection
        self.onCheck = onCheck
        self.onFinish = onFinish
        self.onSkip = onSkip
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                // Topic caption, then the prompt.
                Text(studyQuestion.topicTitle)
                    .font(.subheadline)
                    .foregroundStyle(QuizzlerTheme.textMuted)
                    .accessibilityIdentifier("question-topic")

                Text(studyQuestion.prompt)
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)
                    .accessibilityIdentifier("question-prompt")

                // The verdict is the first thing to read after checking, so it
                // sits directly under the prompt, above the rows it names.
                if case .feedback(let correct) = phase {
                    QuestionVerdictView(correct: correct)
                }

                QuestionRenderer(question: studyQuestion.question, selection: $selection, revealCorrect: isFeedback)
                    .disabled(isFeedback)

                if isFeedback {
                    QuestionExplanationView(explanation: studyQuestion.explanation)

                    // Feedback-only and last: while answering it sat between
                    // the prompt and the verdict and pushed both below the fold.
                    LeitnerProgressCard(
                        storedState: storedReviewState,
                        maximumLevel: maximumLeitnerLevel,
                        isScheduledReview: sessionMode == .srs,
                        answerTimestamp: answerTimestamp,
                        feedbackCorrect: feedbackCorrect,
                        onWhy: { whyPresented = true },
                        onHistory: { historyPresented = true }
                    )
                }
            }
            .padding(QuizzlerTheme.pageGutter)
            .padding(.bottom, QuizzlerTheme.scrollBottomInset)
        }
        .id(studyQuestion.identity)
        .defaultScrollAnchor(.top)
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
        .sheet(isPresented: $historyPresented) {
            QuestionReviewHistorySheet(
                identity: studyQuestion.identity,
                repository: repository,
                purpose: .history,
                scheduledState: storedReviewState,
                maximumLevel: maximumLeitnerLevel,
                answerTimestamp: answerTimestamp
            )
        }
        .sheet(isPresented: $whyPresented) {
            QuestionReviewHistorySheet(
                identity: studyQuestion.identity,
                repository: repository,
                purpose: .why,
                scheduledState: reviewStateAtSessionStart ?? storedReviewState,
                maximumLevel: maximumLeitnerLevel,
                answerTimestamp: answerTimestamp
            )
        }
        .background(QuizzlerTheme.terminalBackground.ignoresSafeArea())
        .onChange(of: selection) { _, newSelection in
            // C4: single-answer types commit as soon as an option is tapped.
            guard !isFeedback, Self.answersOnTap(studyQuestion.question.type) else { return }
            guard !newSelection.isEmpty else { return }
            onCheck(isCorrect)
        }
    }

    // MARK: - Bottom bar (pinned to safe area bottom)

    /// The pinned bar for both phases. Check Answer and Next question share
    /// one primary button in the same slot, so the bar does not jump when the
    /// phase changes.
    private var bottomBar: some View {
        let needsCheckAnswer = !Self.answersOnTap(studyQuestion.question.type)
        return HStack(spacing: 12) {
            // Report flag — left side.
            Button {
                reportPresented = true
            } label: {
                Image(systemName: "flag")
                    .font(.body)
                    .foregroundStyle(QuizzlerTheme.textMuted)
                    .frame(width: QuizzlerTheme.minimumTouchTarget, height: QuizzlerTheme.minimumTouchTarget)
                    .background(isFeedback ? QuizzlerTheme.elevatedCard : .clear, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
            }
            .accessibilityLabel("Report")
            .accessibilityValue("Question ID \(studyQuestion.qid)")
            .accessibilityIdentifier("question-report")

            if !isFeedback && needsCheckAnswer {
                skipButton
            }
            if isFeedback || needsCheckAnswer {
                primaryButton
            } else {
                // Tap-to-answer types have no primary action, so Skip keeps the right side.
                Spacer()
                skipButton
            }
        }
        .padding(.horizontal, QuizzlerTheme.pageGutter)
        .padding(.vertical, 10)
        .background(QuizzlerTheme.terminalBackground)
    }

    private var skipButton: some View {
        Button {
            onSkip()
        } label: {
            Label("Skip", systemImage: "arrow.right")
                .font(.body.weight(.semibold))
                .foregroundStyle(QuizzlerTheme.textMuted)
                .frame(minHeight: QuizzlerTheme.minimumTouchTarget)
        }
        .keyboardShortcut("s", modifiers: [])
        .accessibilityLabel("Skip")
        .accessibilityIdentifier("question-skip")
    }

    /// Check Answer while answering, Next question in feedback, pinned in the
    /// bar's primary slot so a long question can never scroll it under the
    /// bar. Return checks and advances (C4).
    private var primaryButton: some View {
        Button {
            if isFeedback {
                onFinish()
            } else {
                onCheck(isCorrect)
            }
        } label: {
            Text(isFeedback ? "Next question" : "Check Answer")
                .font(.headline)
                .frame(maxWidth: .infinity, minHeight: 48)
        }
        .buttonStyle(.borderedProminent)
        .tint(QuizzlerTheme.primaryCyan)
        .foregroundStyle(.black)
        .disabled(!isFeedback && selection.isEmpty)
        .keyboardShortcut(.return, modifiers: [])
        .accessibilityHint(isFeedback ? "Continue to the next question" : "Check the selected answer")
    }

    // MARK: - Helpers

    private var isFeedback: Bool {
        if case .feedback = phase { return true }
        return false
    }

    private var storedReviewState: SRSState? {
        progressEnvelope?.srs.first(where: { $0.identity == studyQuestion.identity })?.state
    }

    private var feedbackCorrect: Bool? {
        if case .feedback(let correct) = phase { return correct }
        return nil
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
        }
    }

    /// Whether this question type should commit an answer immediately on tap
    /// rather than waiting for an explicit "Check Answer" button press.
    static func answersOnTap(_ type: QuestionType) -> Bool {
        switch type {
        case .multipleChoice, .scenarioMultipleChoice:         true
        case .multipleSelect, .trueFalse, .matching:           false
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
        default:
            return false
        }
    }
}

/// The checked-answer verdict, directly under the prompt so the learner reads
/// it before the marked rows.
private struct QuestionVerdictView: View {
    let correct: Bool

    var body: some View {
        Label(correct ? "Correct" : "Incorrect", systemImage: correct ? "checkmark.circle.fill" : "xmark.circle.fill")
            .font(.headline)
            .foregroundStyle(correct ? QuizzlerTheme.success : QuizzlerTheme.danger)
            .accessibilityLabel(correct ? "Correct" : "Incorrect")
            .accessibilityIdentifier("question-verdict")
    }
}

/// The explanation prose, directly after the choices it is about.
private struct QuestionExplanationView: View {
    let explanation: String

    var body: some View {
        Text(explanation)
            .font(QuizzlerTheme.readableFont)
            .foregroundStyle(QuizzlerTheme.textPrimary)
            .fixedSize(horizontal: false, vertical: true)
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
            .accessibilityIdentifier("question-explanation")
    }
}
