import SwiftUI
import QuizzlerKit

struct LeitnerProgressCard: View {
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
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                LeitnerLevelPie(level: displayedState?.tier, maximumLevel: maximumLevel)
                VStack(alignment: .leading, spacing: 3) {
                    Text("Leitner level")
                        .font(.caption.weight(.bold))
                        .tracking(0.7)
                        .foregroundStyle(QuizzlerTheme.textMuted)
                        .textCase(.uppercase)
                    // Level and next review share a line when the card is wide
                    // enough for both, and stack when it is not.
                    ViewThatFits(in: .horizontal) {
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            levelText
                            nextReviewText
                        }
                        VStack(alignment: .leading, spacing: 2) {
                            levelText
                            nextReviewText
                        }
                    }
                }
                Spacer(minLength: 0)
            }

            HStack(spacing: 12) {
                if isScheduledReview {
                    Button {
                        onWhy()
                    } label: {
                        Text("Why this question?")
                            .frame(minHeight: QuizzlerTheme.minimumTouchTarget)
                    }
                    .accessibilityIdentifier("question-why-this")
                }
                Spacer(minLength: 0)
                Button {
                    onHistory()
                } label: {
                    Text("View history")
                        .frame(minHeight: QuizzlerTheme.minimumTouchTarget)
                }
                .accessibilityIdentifier("question-view-history")
            }
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(QuizzlerTheme.primaryCyan)
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

    private var levelText: some View {
        Text(displayedState.map { "Level \($0.tier) of \(maximumLevel)" } ?? "Not reviewed yet")
            .font(.headline.weight(.semibold))
            .foregroundStyle(QuizzlerTheme.textPrimary)
            .accessibilityIdentifier("question-leitner-level")
    }

    private var nextReviewText: some View {
        Text(nextReviewLabel)
            .font(.footnote)
            .foregroundStyle(QuizzlerTheme.textMuted)
            .accessibilityIdentifier("question-next-review")
    }

    private var nextReviewLabel: String {
        guard let state = displayedState else { return "Answer to begin scheduled reviews" }
        return "\(LeitnerSchedule.intervalLabel(for: state.tier)) interval · \(state.nextDueAt <= Date() ? "Due now" : "Next review \(state.nextDueAt.formatted(date: .abbreviated, time: .omitted))")"
    }
}

struct LeitnerLevelPie: View {
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

struct PieSlice: Shape {
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

struct QuestionReviewHistorySheet: View {
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
