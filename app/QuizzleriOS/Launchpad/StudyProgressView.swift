import Foundation
import SwiftUI
import QuizzlerKit

/// Shows durable study metrics derived from progress history and the active catalog.
///
/// Area accuracy is deliberately excluded: it informs session selection without
/// masquerading as a student-facing score.
struct StudyProgressView: View {
    let insights: StudyInsights
    let scheduledReviewEnabled: Bool
    let persistenceState: LaunchpadProgressModel.PersistenceState
    let onRetrySync: () -> Void
    /// Due and missed work starts here with the same actions Today uses, so
    /// Progress is not a dead end (C12).
    let onStartDueReview: () -> Void
    let onStartRetryMissed: () -> Void
    let onRefresh: () -> Void

    private static let dayFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateStyle = .medium
        formatter.timeStyle = .none
        return formatter
    }()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                coverageSection
                reviewScheduleSection
                recentlyMissedSection
                studyActivitySection
                syncDetail
            }
            .padding(QuizzlerTheme.pageGutter)
            .padding(.bottom, QuizzlerTheme.scrollBottomInset)
        }
        .refreshable { onRefresh() }
        .background(QuizzlerTheme.terminalBackground.ignoresSafeArea())
        .navigationTitle("Progress")
    }

    private var coverageSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionHeader("Coverage")

            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    HStack(spacing: 2) {
                        Text("Questions seen")
                            .font(.headline)
                            .foregroundStyle(QuizzlerTheme.textPrimary)
                        InfoPopoverButton(
                            title: "Questions seen",
                            message: "Distinct pack questions encountered",
                            identifier: "info-coverage"
                        )
                    }
                    Spacer()
                    // Read-only totals wear text colour, not the primary
                    // action cyan, which must mark tappable work (C12).
                    Text("\(insights.coverage.seen) of \(insights.coverage.totalQuestions)")
                        .font(.title3.monospacedDigit().weight(.semibold))
                        .foregroundStyle(QuizzlerTheme.textPrimary)
                }
                .padding(16)
                .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))

                HStack {
                    HStack(spacing: 2) {
                        Text("Attempts")
                            .font(.headline)
                            .foregroundStyle(QuizzlerTheme.textPrimary)
                        InfoPopoverButton(
                            title: "Attempts",
                            message: "Total answers submitted (not distinct questions)",
                            identifier: "info-attempts"
                        )
                    }
                    Spacer()
                    Text("\(insights.coverage.correct) of \(insights.coverage.answered)")
                        .font(.title3.monospacedDigit().weight(.semibold))
                        .foregroundStyle(QuizzlerTheme.textPrimary)
                        .accessibilityIdentifier("progress-attempts")
                }
                .padding(16)
                .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("progress-coverage")
    }

    private var reviewScheduleSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionHeader("Review schedule")

            if scheduledReviewEnabled {
                VStack(spacing: 12) {
                    if insights.due.due > 0 {
                        dueNowRow
                    } else {
                        scheduleRow(
                            label: "Due now",
                            value: "\(insights.due.due)",
                            highlight: false
                        )
                    }
                    Divider().background(QuizzlerTheme.border)
                    scheduleRow(
                        label: "Upcoming",
                        value: "\(insights.due.upcoming)",
                        highlight: false
                    )
                    Divider().background(QuizzlerTheme.border)
                    scheduleRow(
                        label: "Not yet scheduled",
                        value: "\(insights.due.unscheduled)",
                        highlight: false
                    )
                }
                .padding(16)
                .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
            } else {
                HStack(spacing: 2) {
                    Text("Scheduled review paused")
                        .font(.headline)
                        .foregroundStyle(QuizzlerTheme.textPrimary)
                    InfoPopoverButton(
                        title: "Scheduled review paused",
                        message: "Previously seen questions stay saved. Turn it on in Settings to resume scheduled review.",
                        identifier: "info-scheduled-paused"
                    )
                    Spacer()
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
            }
        }
    }

    private func scheduleRow(label: String, value: String, highlight: Bool) -> some View {
        HStack {
            Text(label)
                .font(.subheadline)
                .foregroundStyle(QuizzlerTheme.textPrimary)
            Spacer()
            Text(value)
                .font(.title3.monospacedDigit().weight(.semibold))
                .foregroundStyle(highlight ? QuizzlerTheme.primaryCyan : QuizzlerTheme.textMuted)
        }
    }

    /// A non-zero due count is work the learner can start right here, so the
    /// row is the same kind of action Today offers (C12). A zero count stays
    /// a read-only row in text colour.
    private var dueNowRow: some View {
        Button(action: onStartDueReview) {
            HStack {
                Text("Due now")
                    .font(.subheadline)
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                Spacer()
                Text("\(insights.due.due)")
                    .font(.title3.monospacedDigit().weight(.semibold))
                    .foregroundStyle(QuizzlerTheme.primaryCyan)
                Image(systemName: "arrow.right.circle.fill")
                    .font(.body)
                    .foregroundStyle(QuizzlerTheme.primaryCyan)
            }
            .frame(minHeight: QuizzlerTheme.minimumTouchTarget)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Start due review")
        .accessibilityValue("\(insights.due.due) questions due now")
        .accessibilityIdentifier("progress-start-due-review")
    }

    private var recentlyMissedSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 2) {
                sectionHeader("Recently missed")
                InfoPopoverButton(
                    title: "Recently missed",
                    message: "History is bounded to roughly the last 200 answers.",
                    identifier: "info-history-bound"
                )
                Spacer()
            }

            VStack(alignment: .leading, spacing: 8) {
                missedQuestionsRow
            }
            .padding(16)
            .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
        }
    }

    /// Missed work starts here too (C12): a non-zero count is a retry action
    /// in the same style Today uses, while an empty history stays a read-only
    /// row whose count wears text colour.
    @ViewBuilder private var missedQuestionsRow: some View {
        if insights.recentMisses.isEmpty {
            HStack {
                Text("Missed questions")
                    .font(.headline)
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                Spacer()
                Text("\(insights.recentMisses.count)")
                    .font(.title3.monospacedDigit().weight(.semibold))
                    .foregroundStyle(QuizzlerTheme.textMuted)
            }
        } else {
            Button(action: onStartRetryMissed) {
                HStack {
                    Text("Missed questions")
                        .font(.headline)
                        .foregroundStyle(QuizzlerTheme.textPrimary)
                    Spacer()
                    Text("\(insights.recentMisses.count)")
                        .font(.title3.monospacedDigit().weight(.semibold))
                        .foregroundStyle(QuizzlerTheme.warning)
                    Image(systemName: "arrow.right.circle.fill")
                        .font(.body)
                        .foregroundStyle(QuizzlerTheme.primaryCyan)
                }
                .frame(minHeight: QuizzlerTheme.minimumTouchTarget)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Retry missed questions")
            .accessibilityValue("\(insights.recentMisses.count) missed questions")
            .accessibilityIdentifier("progress-retry-missed")
        }
    }

    private var studyActivitySection: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionHeader("Study activity")

            VStack(alignment: .leading, spacing: 12) {
                let maxAnswered = insights.activity.map(\.answered).max() ?? 0

                HStack(alignment: .bottom, spacing: 6) {
                    ForEach(insights.activity.indices, id: \.self) { index in
                        let day = insights.activity[index]
                        let ratio = maxAnswered > 0 ? CGFloat(day.answered) / CGFloat(maxAnswered) : 0
                        let barHeight = day.answered > 0 ? max(8, ratio * 50) : 4

                        VStack(spacing: 0) {
                            Spacer(minLength: 0)
                            RoundedRectangle(cornerRadius: 2)
                                .fill(day.answered > 0 ? QuizzlerTheme.primaryCyan : QuizzlerTheme.border)
                                .frame(height: barHeight)
                        }
                        .frame(maxWidth: .infinity, maxHeight: 54)
                        .accessibilityElement(children: .ignore)
                        .accessibilityLabel(activityAccessibilityLabel(for: day))
                    }
                }
                .frame(height: 54)

                HStack {
                    Text("14 days ago")
                        .font(.caption2)
                        .foregroundStyle(QuizzlerTheme.textMuted)
                    Spacer()
                    Text("Today")
                        .font(.caption2)
                        .foregroundStyle(QuizzlerTheme.textMuted)
                }
            }
            .padding(16)
            .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
        }
    }

    private func activityAccessibilityLabel(for day: StudyDay) -> String {
        let dateString = Self.dayFormatter.string(from: day.day)
        if day.answered == 0 {
            return "\(dateString): no questions answered"
        } else {
            return "\(dateString): \(day.answered) answered, \(day.correct) correct"
        }
    }

    /// The pinned header badge already states sync status, so this space
    /// keeps only the pending state's retry action (C14).
    @ViewBuilder private var syncDetail: some View {
        if case .syncPending = persistenceState {
            Button("Retry sync", action: onRetrySync)
                .buttonStyle(.bordered)
                .tint(QuizzlerTheme.primaryCyan)
        }
    }

    private func sectionHeader(_ text: String) -> some View {
        Text(text.uppercased())
            .font(QuizzlerTheme.metadataFont)
            .foregroundStyle(QuizzlerTheme.primaryCyan)
    }
}
