import Foundation
import SwiftUI
import QuizzlerKit

/// Shows durable study metrics derived from progress history and the active catalog.
///
/// Area accuracy is deliberately excluded: it informs session selection without
/// masquerading as a student-facing score.
struct StudyProgressView: View {
    let insights: StudyInsights
    let persistenceState: LaunchpadProgressModel.PersistenceState
    let onRetrySync: () -> Void

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
        }
        .background(QuizzlerTheme.terminalBackground.ignoresSafeArea())
        .navigationTitle("Progress")
    }

    private var coverageSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionHeader("Coverage")

            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Questions seen")
                            .font(.headline)
                            .foregroundStyle(QuizzlerTheme.textPrimary)
                        Text("Distinct pack questions encountered")
                            .font(.caption)
                            .foregroundStyle(QuizzlerTheme.textMuted)
                    }
                    Spacer()
                    Text("\(insights.coverage.seen) of \(insights.coverage.totalQuestions)")
                        .font(.title3.monospacedDigit().weight(.semibold))
                        .foregroundStyle(QuizzlerTheme.primaryCyan)
                }
                .padding(16)
                .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))

                HStack {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Attempts")
                            .font(.headline)
                            .foregroundStyle(QuizzlerTheme.textPrimary)
                        Text("Total answers submitted (not distinct questions)")
                            .font(.caption)
                            .foregroundStyle(QuizzlerTheme.textMuted)
                    }
                    Spacer()
                    Text("\(insights.coverage.correct) of \(insights.coverage.answered)")
                        .font(.title3.monospacedDigit().weight(.semibold))
                        .foregroundStyle(QuizzlerTheme.primaryCyan)
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

            VStack(spacing: 12) {
                scheduleRow(
                    label: "Due now",
                    value: "\(insights.due.due)",
                    highlight: insights.due.due > 0
                )
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

    private var recentlyMissedSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionHeader("Recently missed")

            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("Missed questions")
                        .font(.headline)
                        .foregroundStyle(QuizzlerTheme.textPrimary)
                    Spacer()
                    Text("\(insights.recentMisses.count)")
                        .font(.title3.monospacedDigit().weight(.semibold))
                        .foregroundStyle(insights.recentMisses.isEmpty ? QuizzlerTheme.textMuted : QuizzlerTheme.warning)
                }
                Text("History is bounded to roughly the last 200 answers.")
                    .font(.caption)
                    .foregroundStyle(QuizzlerTheme.textMuted)
            }
            .padding(16)
            .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
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

    @ViewBuilder private var syncDetail: some View {
        switch persistenceState {
        case .synced:
            Label("Progress is synced through iCloud.", systemImage: "checkmark.icloud")
                .font(.subheadline)
                .foregroundStyle(QuizzlerTheme.textMuted)
        case .syncing:
            Label("Syncing progress with iCloud…", systemImage: "arrow.triangle.2.circlepath")
                .font(.subheadline)
                .foregroundStyle(QuizzlerTheme.textMuted)
                .accessibilityLabel("Syncing progress with iCloud")
        case .syncPending:
            VStack(alignment: .leading, spacing: 8) {
                Text("Progress is saved on this device. iCloud needs another try.")
                    .font(.subheadline)
                    .foregroundStyle(QuizzlerTheme.textMuted)
                Button("Retry sync", action: onRetrySync)
                    .buttonStyle(.bordered)
                    .tint(QuizzlerTheme.primaryCyan)
            }
        case .accountChanged:
            Text("This device has a different iCloud account. Your study history is safe here. Sign in to the original account to resume syncing.")
                .font(.subheadline)
                .foregroundStyle(QuizzlerTheme.textMuted)
        case .loading, .local, .saving, .saveFailed:
            Text("Progress is stored on this device.")
                .font(.subheadline)
                .foregroundStyle(QuizzlerTheme.textMuted)
        }
    }

    private func sectionHeader(_ text: String) -> some View {
        Text(text.uppercased())
            .font(QuizzlerTheme.metadataFont)
            .foregroundStyle(QuizzlerTheme.primaryCyan)
    }
}
