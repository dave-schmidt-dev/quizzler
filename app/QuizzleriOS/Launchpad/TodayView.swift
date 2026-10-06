import Foundation
import SwiftUI
import QuizzlerKit
import UIKit

/// The first screen a tester sees. Every number on it comes from the installed
/// pack or the progress repository.
struct TodayView: View {
    let questionNumber: Int
    let questionCount: Int
    let unseenCount: Int
    let dueCount: Int
    let scheduledReviewEnabled: Bool
    let sessionLength: Int
    let missedCount: Int
    let maximumLeitnerLevel: Int
    /// The CS0-004 lab is course material: Today offers it only while its own
    /// course is the selected one.
    let showsLearningLab: Bool
    /// `nil` when the selected pack has no session to continue. Non-nil is
    /// the full row title, e.g. "Resume session · 3 of 10" (C3).
    let resumeLabel: String?
    let onResume: () -> Void
    let onStart: () -> Void
    let onStartLearnNew: () -> Void
    let onStartDueReview: () -> Void
    let onStartRetryMissed: () -> Void
    let onChooseNextSessionLength: (Int) -> Void
    let onOpenLab: () -> Void
    /// Pull-to-refresh runs the same synchronize-on-foreground path the
    /// scene-phase change uses (C13), so refresh is a gesture rather than a
    /// hidden tap on the status badge.
    @EnvironmentObject private var progress: LaunchpadProgressModel
    @State private var reviewExplanationPresented = false
    @AppStorage(QuietPowerShellLabKeys.completed) private var labCompleted = false

    private var recommendation: TodayRecommendation {
        let limit = StudySessionLength.limit(stored: sessionLength, packQuestionCount: questionCount)
        return TodayRecommendation(
            due: dueCount,
            unseen: unseenCount,
            sessionLimit: limit,
            scheduledReviewEnabled: scheduledReviewEnabled
        )
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                if let resumeLabel {
                    resumeSessionRow(title: resumeLabel)
                }

                heroCard

                quietListCard
            }
            .padding(QuizzlerTheme.pageGutter)
            .padding(.top, 16)
            .padding(.bottom, QuizzlerTheme.scrollBottomInset)
        }
        .refreshable { progress.synchronizeOnForeground() }
        .background(QuizzlerTheme.terminalBackground)
        .navigationTitle("Today")
        .toolbar(.hidden, for: .navigationBar)
    }

    /// A saved session continues above everything else: it is the action the
    /// learner was already in the middle of (C3).
    private func resumeSessionRow(title: String) -> some View {
        Button(action: onResume) {
            HStack {
                Text(title)
                    .font(.body.weight(.semibold))
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                    .lineLimit(1)
                Spacer()
                Image(systemName: "arrow.right.circle.fill")
                    .font(.body)
                    .foregroundStyle(QuizzlerTheme.primaryCyan)
            }
            .padding(.horizontal, 16)
            .frame(maxWidth: .infinity, minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(title)
        .accessibilityIdentifier("today-resume-session")
        .todayActionSurface()
    }

    private var heroCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 4) {
                Text(recommendation.title)
                    .font(.title2.weight(.bold))
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                Text(recommendation.detail)
                    .font(.subheadline)
                    .foregroundStyle(QuizzlerTheme.textMuted)
            }

            heroStartButton

            Button("How reviews work") {
                reviewExplanationPresented = true
            }
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(QuizzlerTheme.primaryCyan)
            .accessibilityIdentifier("today-how-reviews-work")
        }
        .padding(18)
        .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
        .overlay(
            RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius)
                .stroke(QuizzlerTheme.primaryCyan.opacity(0.3), lineWidth: 1)
        )
        .sheet(isPresented: $reviewExplanationPresented) {
            ScheduledReviewsExplanationView(maximumLevel: maximumLeitnerLevel)
        }
    }

    /// While the hero is the learning entry point it also carries the
    /// pack-order position the Learn row stated (C14).
    @ViewBuilder private var heroStartButton: some View {
        if showsLearnNewRow {
            heroStartCore
        } else {
            heroStartCore
                .accessibilityValue("Question \(questionNumber) of \(questionCount)")
        }
    }

    private var heroStartCore: some View {
        Button(action: {
            switch recommendation {
            case .review:
                onStartDueReview()
            case .learn:
                onStartLearnNew()
            case .caughtUp:
                onStart()
            }
        }) {
            Text(recommendation.buttonTitle)
                .font(.body.weight(.semibold))
                .frame(maxWidth: .infinity, minHeight: 48)
        }
        .buttonStyle(.borderedProminent)
        .tint(QuizzlerTheme.primaryCyan)
        .foregroundStyle(.black)
        .accessibilityLabel(recommendation.buttonTitle)
        .accessibilityIdentifier("today-hero-start")
    }

    private var quietListCard: some View {
        VStack(spacing: 10) {
            if showsLearnNewRow {
                learnNewRow
            }
            retryMissedRow
            sessionLengthRow
            if showsLearningLab {
                learningLabRow
            }
        }
    }

    /// The hero already starts learning while it offers it, so the Learn row
    /// appears only while the hero is busy with due reviews instead (C14).
    private var showsLearnNewRow: Bool {
        recommendation.isReview
    }

    private var learnNewRow: some View {
        Button(action: onStartLearnNew) {
            HStack {
                Text("Learn new questions")
                    .font(.body)
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                Spacer()
                Text("\(unseenCount)")
                    .font(.body.monospacedDigit())
                    .foregroundStyle(QuizzlerTheme.textMuted)
                Image(systemName: "arrow.right.circle.fill")
                    .font(.body)
                    .foregroundStyle(QuizzlerTheme.primaryCyan)
            }
            .padding(.horizontal, 16)
            .frame(maxWidth: .infinity, minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Learn new questions")
        .accessibilityIdentifier("today-learn-new")
        .accessibilityValue("Question \(questionNumber) of \(questionCount)")
        .todayActionSurface()
    }

    private var retryMissedRow: some View {
        Button(action: onStartRetryMissed) {
            HStack {
                Text("Retry missed")
                    .font(.body)
                    .foregroundStyle(missedCount > 0 ? QuizzlerTheme.textPrimary : QuizzlerTheme.textMuted)
                Spacer()
                Text(missedBatchCount == missedCount ? "\(missedCount)" : "\(missedBatchCount) of \(missedCount)")
                    .font(.body.monospacedDigit())
                    .foregroundStyle(QuizzlerTheme.textMuted)
                Image(systemName: "arrow.right.circle.fill")
                    .font(.body)
                    .foregroundStyle(missedCount > 0 ? QuizzlerTheme.primaryCyan : QuizzlerTheme.textMuted)
            }
            .padding(.horizontal, 16)
            .frame(maxWidth: .infinity, minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(missedCount == 0)
        .accessibilityLabel("Retry missed")
        .accessibilityIdentifier("today-retry-missed")
        .accessibilityValue(
            missedCount == 0
                ? "No missed questions"
                : "Next session: \(missedBatchCount) of \(missedCount) missed questions"
        )
        .opacity(missedCount == 0 ? 0.55 : 1)
        .todayActionSurface()
    }

    private var missedBatchCount: Int {
        StudySessionLength.limit(stored: sessionLength, packQuestionCount: missedCount)
    }

    private var sessionLengthRow: some View {
        Menu {
            ForEach(StudySessionLength.options, id: \.self) { option in
                Button {
                    onChooseNextSessionLength(option)
                } label: {
                    HStack {
                        Text(StudySessionLength.label(option))
                        if sessionLength == option {
                            Image(systemName: "checkmark")
                        }
                    }
                }
            }
        } label: {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Session length")
                        .font(.body)
                        .foregroundStyle(QuizzlerTheme.textPrimary)
                    Text("Next session only")
                        .font(.caption)
                        .foregroundStyle(QuizzlerTheme.textMuted)
                }
                Spacer()
                Text(StudySessionLength.maximumLabel(sessionLength))
                    .font(.body)
                    .foregroundStyle(QuizzlerTheme.textMuted)
                Image(systemName: "chevron.up.chevron.down")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(QuizzlerTheme.primaryCyan)
            }
            .padding(.horizontal, 16)
            .frame(maxWidth: .infinity, minHeight: 44)
            .contentShape(Rectangle())
        }
        .accessibilityLabel("Session length")
        .accessibilityValue(StudySessionLength.maximumLabel(sessionLength))
        .accessibilityHint("Applies only to the next session")
        .accessibilityIdentifier("today-session-length")
        .todayActionSurface()
    }

    private var learningLabRow: some View {
        Button(action: onOpenLab) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("CS0-004 learning lab")
                        .font(.body)
                        .foregroundStyle(QuizzlerTheme.textPrimary)
                    Text("Synthetic practice • Not exam readiness")
                        .font(.caption)
                        .foregroundStyle(QuizzlerTheme.textMuted)
                }
                Spacer()
                if labCompleted {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.body)
                        .foregroundStyle(QuizzlerTheme.success)
                }
                Image(systemName: "arrow.right.circle.fill")
                    .font(.body)
                    .foregroundStyle(QuizzlerTheme.primaryCyan)
            }
            .padding(.horizontal, 16)
            .frame(maxWidth: .infinity, minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("CS0-004 learning lab")
        .accessibilityIdentifier("today-learning-lab")
        .todayActionSurface()
    }

}

struct ScheduledReviewsExplanationView: View {
    let maximumLevel: Int
    @Environment(\.dismiss) private var dismiss

    private var intervals: String {
        (1...max(1, min(maximumLevel, 7)))
            .compactMap(LeitnerSchedule.intervalDays(for:))
            .map { "\($0) \($0 == 1 ? "day" : "days")" }
            .joined(separator: ", ")
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("Each answered question has a Leitner level from 1 to your maximum of \(maximumLevel). The review interval for each level is \(intervals).")
                    Text("A correct answer moves up one level, stopping at your maximum. A missed answer moves down two levels, stopping at level 1.")
                    Text("A question is due when its next review date arrives. Today offers due questions first. Session length is a maximum, so a session with fewer due questions contains fewer questions.")
                    Text("Turning off Offer scheduled reviews hides that suggestion on Today. Your levels, review dates, and history remain saved.")
                    Text("Lowering your maximum brings longer review dates forward and records the change in each affected question’s history.")
                    Link(destination: URL(string: "https://en.wikipedia.org/wiki/Spaced_repetition")!) {
                        Label("Spaced repetition on Wikipedia", systemImage: "arrow.up.right.square")
                    }
                    .accessibilityIdentifier("scheduled-reviews-wikipedia-link")
                }
                .font(.body)
                .foregroundStyle(QuizzlerTheme.textPrimary)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(QuizzlerTheme.pageGutter)
            }
            .background(QuizzlerTheme.terminalBackground)
            .navigationTitle("How reviews work")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                        .accessibilityIdentifier("scheduled-reviews-done")
                }
            }
        }
        .preferredColorScheme(.dark)
    }
}
