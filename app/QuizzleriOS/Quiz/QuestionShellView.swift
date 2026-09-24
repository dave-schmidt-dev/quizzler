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

                QuestionRenderer(question: studyQuestion.question, selection: $selection, revealCorrect: isFeedback)
                    .disabled(isFeedback)

                LeitnerProgressCard(
                    storedState: storedReviewState,
                    maximumLevel: maximumLeitnerLevel,
                    isScheduledReview: sessionMode == .srs,
                    answerTimestamp: answerTimestamp,
                    feedbackCorrect: feedbackCorrect,
                    onWhy: { whyPresented = true },
                    onHistory: { historyPresented = true }
                )

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

private struct LeitnerProgressCard: View {
    let storedState: SRSState?
    let maximumLevel: Int
    let isScheduledReview: Bool
    let answerTimestamp: Date?
    let feedbackCorrect: Bool?
    let onWhy: () -> Void
    let onHistory: () -> Void

    private var displayedState: SRSState? {
        guard let feedbackCorrect else { return storedState }
        if let storedState, storedState.lastReviewedAt == answerTimestamp {
            return storedState
        }
        let nextLevel = LeitnerSchedule.nextLevel(
            current: storedState?.tier,
            correct: feedbackCorrect,
            maximum: maximumLevel
        )
        guard let interval = LeitnerSchedule.intervalDays(for: nextLevel),
              let answeredAt = answerTimestamp else { return storedState }
        return try? SRSState(
            tier: nextLevel,
            nextDueAt: answeredAt.addingTimeInterval(TimeInterval(interval * 86_400)),
            lastReviewedAt: answeredAt,
            intervalDays: interval,
            reviewCount: (storedState?.reviewCount ?? 0) + 1
        )
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 12) {
                LeitnerLevelPie(level: displayedState?.tier, maximumLevel: maximumLevel)
                VStack(alignment: .leading, spacing: 3) {
                    Text("Leitner level")
                        .font(.caption.weight(.bold))
                        .tracking(0.7)
                        .foregroundStyle(QuizzlerTheme.textMuted)
                        .textCase(.uppercase)
                    Text(displayedState.map { "\($0.tier) of \(maximumLevel)" } ?? "Not reviewed yet")
                        .font(.headline.weight(.semibold))
                        .foregroundStyle(QuizzlerTheme.textPrimary)
                        .accessibilityIdentifier("question-leitner-level")
                }
                Spacer(minLength: 0)
            }

            if let state = displayedState {
                Text("\(LeitnerSchedule.intervalLabel(for: state.tier)) interval · \(state.nextDueAt <= Date() ? "Due now" : "Next review \(state.nextDueAt.formatted(date: .abbreviated, time: .omitted))")")
                    .font(.footnote)
                    .foregroundStyle(QuizzlerTheme.textMuted)
                    .accessibilityIdentifier("question-next-review")
            } else {
                Text("Answer to begin scheduled reviews")
                    .font(.footnote)
                    .foregroundStyle(QuizzlerTheme.textMuted)
                    .accessibilityIdentifier("question-next-review")
            }

            HStack(spacing: 12) {
                if isScheduledReview {
                    Button("Why this question?") { onWhy() }
                        .accessibilityIdentifier("question-why-this")
                }
                Spacer(minLength: 0)
                Button("View history") { onHistory() }
                    .accessibilityIdentifier("question-view-history")
            }
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(QuizzlerTheme.primaryCyan)
            .padding(.top, 8)
            .overlay(alignment: .top) {
                Rectangle()
                    .fill(QuizzlerTheme.border)
                    .frame(height: 1)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
        .overlay(
            RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius)
                .stroke(QuizzlerTheme.border, lineWidth: 1)
        )
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("question-leitner-card")
    }
}

private struct LeitnerLevelPie: View {
    let level: Int?
    let maximumLevel: Int

    private var safeMaximum: Int { min(7, max(1, maximumLevel)) }

    var body: some View {
        ZStack {
            ForEach(0..<safeMaximum, id: \.self) { index in
                let slice = PieSlice(
                    startDegrees: -90 + Double(index) * 360 / Double(safeMaximum) + 2,
                    endDegrees: -90 + Double(index + 1) * 360 / Double(safeMaximum) - 2
                )
                slice
                    .fill(index < (level ?? 0) ? filledColor(for: index) : QuizzlerTheme.border)
                    .overlay(slice.stroke(QuizzlerTheme.elevatedCard, lineWidth: 1.5))
            }
            Circle()
                .stroke(QuizzlerTheme.textMuted.opacity(0.7), lineWidth: 1)
        }
        .frame(width: 48, height: 48)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(level.map { "Leitner level \($0) of \(safeMaximum)" } ?? "Leitner level, not reviewed yet")
        .accessibilityIdentifier("question-leitner-pie")
    }

    private func filledColor(for index: Int) -> Color {
        let hue: Double
        if safeMaximum == 1 {
            hue = 0.34
        } else {
            hue = 0.34 * Double(index) / Double(safeMaximum - 1)
        }
        return Color(hue: hue, saturation: 0.72, brightness: 0.86)
    }
}

private struct PieSlice: Shape {
    let startDegrees: Double
    let endDegrees: Double

    func path(in rect: CGRect) -> Path {
        let center = CGPoint(x: rect.midX, y: rect.midY)
        let radius = min(rect.width, rect.height) / 2
        var path = Path()
        path.move(to: center)
        path.addLine(to: CGPoint(
            x: center.x + radius * cos(startDegrees * .pi / 180),
            y: center.y + radius * sin(startDegrees * .pi / 180)
        ))
        path.addArc(
            center: center,
            radius: radius,
            startAngle: .degrees(startDegrees),
            endAngle: .degrees(endDegrees),
            clockwise: false
        )
        path.closeSubpath()
        return path
    }
}

private struct QuestionReviewHistorySheet: View {
    enum Purpose: Equatable { case history, why }

    let identity: QuestionIdentity
    let repository: any LaunchpadProgressRepository
    let purpose: Purpose
    let scheduledState: SRSState?
    let maximumLevel: Int
    let answerTimestamp: Date?
    @Environment(\.dismiss) private var dismiss
    @State private var history: QuestionReviewHistory?
    @State private var failed = false

    private var title: String { purpose == .why ? "Why this question?" : "Question history" }

    var body: some View {
        NavigationStack {
            Group {
                if failed {
                    ContentUnavailableView("History unavailable", systemImage: "clock.badge.exclamationmark", description: Text("Question history could not be loaded. Try again when progress sync is available."))
                } else if let history {
                    historyContent(history)
                } else {
                    ProgressView("Loading history…")
                        .tint(QuizzlerTheme.primaryCyan)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(QuizzlerTheme.terminalBackground)
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .preferredColorScheme(.dark)
        .task(id: identity) {
            do {
                history = try await repository.reviewHistory(for: identity)
            } catch {
                failed = true
            }
        }
    }

    @ViewBuilder
    private func historyContent(_ result: QuestionReviewHistory) -> some View {
        let displayedEvents = eventsForDisplay(result.events)
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                if purpose == .why {
                    whySummary(events: displayedEvents, isComplete: result.isComplete)
                } else {
                    historySummary(events: displayedEvents, isComplete: result.isComplete)
                }

                if displayedEvents.isEmpty {
                    Text(emptyHistoryCopy(isComplete: result.isComplete))
                        .font(.body)
                        .foregroundStyle(QuizzlerTheme.textMuted)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(14)
                        .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
                        .accessibilityIdentifier("question-history-empty")
                } else {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(Array(displayedEvents.reversed())) { event in
                            historyRow(event)
                        }
                    }
                    .padding(.horizontal, 14)
                    .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
                }
            }
            .padding(QuizzlerTheme.pageGutter)
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier(purpose == .why ? "question-why-sheet" : "question-history-sheet")
    }

    private func eventsForDisplay(_ events: [QuestionReviewEvent]) -> [QuestionReviewEvent] {
        guard purpose == .why, let answerTimestamp else { return events }
        return events.filter { $0.eventTime < answerTimestamp }
    }

    private func whySummary(events: [QuestionReviewEvent], isComplete: Bool) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            if let scheduledState {
                Text("This question was scheduled for review on \(scheduledState.nextDueAt.formatted(date: .long, time: .omitted)). Its level was \(scheduledState.tier) of \(maximumLevel), with a \(LeitnerSchedule.intervalLabel(for: scheduledState.tier)) interval.")
                    .font(.body)
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                if let lastAnswer = events.last(where: { $0.outcome == .correct || $0.outcome == .missed }) {
                    Text("Your last answer was \(outcomeLabel(lastAnswer.outcome).lowercased()) on \(lastAnswer.eventTime.formatted(date: .long, time: .omitted)); it set this review date.")
                        .font(.subheadline)
                        .foregroundStyle(QuizzlerTheme.textMuted)
                } else {
                    Text(isComplete ? "No earlier answer event is available for this question." : "The earlier answer date is unavailable because this question’s pre-upgrade history was not retained.")
                        .font(.subheadline)
                        .foregroundStyle(QuizzlerTheme.textMuted)
                }
            } else {
                Text("This question was selected because its saved review date had arrived.")
                    .font(.body)
                    .foregroundStyle(QuizzlerTheme.textPrimary)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
        .accessibilityIdentifier("question-why-summary")
    }

    private func historySummary(events: [QuestionReviewEvent], isComplete: Bool) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(scheduledState.map { "Level \($0.tier) of \(maximumLevel)" } ?? "Not reviewed yet")
                .font(.headline)
                .foregroundStyle(QuizzlerTheme.textPrimary)
            if let scheduledState {
                Text("\(LeitnerSchedule.intervalLabel(for: scheduledState.tier)) interval · \(scheduledState.nextDueAt <= Date() ? "Due now" : "Next review \(scheduledState.nextDueAt.formatted(date: .abbreviated, time: .omitted))")")
                    .font(.subheadline)
                    .foregroundStyle(QuizzlerTheme.textMuted)
            }
            Text(isComplete ? "Complete history · \(events.count) events" : "Partial history · earlier events may be unavailable")
                .font(.caption)
                .foregroundStyle(QuizzlerTheme.textMuted)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
        .accessibilityIdentifier("question-history-summary")
    }

    private func historyRow(_ event: QuestionReviewEvent) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(alignment: .firstTextBaseline) {
                Text(event.eventTime.formatted(date: .abbreviated, time: .shortened))
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                Spacer(minLength: 8)
                Text(outcomeLabel(event.outcome))
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(outcomeColor(event.outcome))
            }
            Text("Level \(event.priorLevel) → \(event.resultingLevel) · Next review \(event.resultingDueAt.formatted(date: .abbreviated, time: .omitted))")
                .font(.footnote)
                .foregroundStyle(QuizzlerTheme.textMuted)
        }
        .padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .overlay(alignment: .bottom) { Rectangle().fill(QuizzlerTheme.border).frame(height: 1) }
        .accessibilityIdentifier("question-history-event-\(event.id)")
    }

    private func emptyHistoryCopy(isComplete: Bool) -> String {
        isComplete
            ? "No review history yet for this question."
            : "Earlier review history is unavailable. New answers and schedule changes will appear here after they sync."
    }

    private func outcomeLabel(_ outcome: QuestionReviewOutcome) -> String {
        switch outcome {
        case .correct: "Correct"
        case .missed: "Missed"
        case .maximumLevelChanged: "Maximum changed"
        }
    }

    private func outcomeColor(_ outcome: QuestionReviewOutcome) -> Color {
        switch outcome {
        case .correct: QuizzlerTheme.success
        case .missed: QuizzlerTheme.warning
        case .maximumLevelChanged: QuizzlerTheme.primaryCyan
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
