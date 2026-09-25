import Foundation
import SwiftUI
import QuizzlerKit
import UIKit

/// Compact, live persistence status reserved above every top-level study
/// surface. Successful sync and failure states are controls so their recovery
/// and refresh actions remain reachable without relying on a stale,
/// screen-local status string.
struct GlobalProgressStatusControl: View {
    @ObservedObject var progress: LaunchpadProgressModel

    private var state: LaunchpadProgressModel.PersistenceState {
        progress.persistenceState
    }

    static let textColor = QuizzlerTheme.textPrimary

    static func compactLabel(for state: LaunchpadProgressModel.PersistenceState) -> String {
        switch state {
        case .loading: "Loading"
        case .local: "Saved"
        case .saving: "Saving"
        case .syncing: "Syncing"
        case .synced: "Synced"
        case .syncPending: "Pending sync"
        case .accountChanged: "Account changed"
        case .saveFailed: "Retry save"
        }
    }

    static func icon(for state: LaunchpadProgressModel.PersistenceState) -> String {
        switch state {
        case .synced: "checkmark.icloud.fill"
        case .saving, .syncing: "arrow.triangle.2.circlepath"
        case .syncPending, .accountChanged, .saveFailed: "exclamationmark.icloud.fill"
        case .loading: "circle.dotted"
        case .local: "internaldrive"
        }
    }

    static func iconColor(for state: LaunchpadProgressModel.PersistenceState) -> Color {
        switch state {
        case .synced: QuizzlerTheme.success
        case .syncPending, .accountChanged, .saveFailed: QuizzlerTheme.danger
        case .loading, .local, .saving, .syncing: QuizzlerTheme.textMuted
        }
    }

    private var isRetryable: Bool {
        state == .syncPending || state == .saveFailed
    }

    private var icon: String {
        Self.icon(for: state)
    }

    private var iconColor: Color {
        Self.iconColor(for: state)
    }

    var body: some View {
        if state == .synced {
            Button(action: progress.synchronizeOnForeground) {
                statusLabel
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("global-progress-status")
            .accessibilityLabel(Self.compactLabel(for: state))
            .accessibilityHint("Checks for updates")
        } else if isRetryable {
            Button(action: progress.saveCurrentSession) {
                statusLabel
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("global-progress-status")
            .accessibilityLabel(progress.persistenceStatus)
            .accessibilityHint(state == .saveFailed ? "Retries saving recorded progress" : "Retries iCloud synchronization")
        } else {
            statusLabel
                .accessibilityIdentifier("global-progress-status")
                .accessibilityLabel(progress.persistenceStatus)
        }
    }

    private var statusLabel: some View {
        Label {
            Text(Self.compactLabel(for: state))
                .foregroundStyle(Self.textColor)
        } icon: {
            Image(systemName: icon)
                .foregroundStyle(iconColor)
        }
        .font(.caption.weight(.semibold))
        .lineLimit(1)
        .fixedSize(horizontal: true, vertical: false)
        .padding(.horizontal, 10)
        .frame(minHeight: QuizzlerTheme.minimumTouchTarget)
        .background(QuizzlerTheme.raisedCard, in: Capsule())
        .overlay(
            Capsule().stroke(iconColor.opacity(0.45), lineWidth: 1)
        )
        .accessibilityElement(children: .ignore)
    }
}

/// Lists all installed course packs and lets the learner switch courses.
struct CoursesView: View {
    @ObservedObject var catalog: StudyCatalogModel
    @ObservedObject var progress: LaunchpadProgressModel
    let scheduledReviewEnabled: Bool
    let onSelectCourse: (String) -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Your courses")
                    .font(.title2.weight(.bold))
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .accessibilityAddTraits(.isHeader)
                    .accessibilityIdentifier("courses-heading")

                ForEach(catalog.availablePacks) { pack in
                    courseCard(for: pack)
                }
            }
            .padding(QuizzlerTheme.pageGutter)
            .padding(.bottom, QuizzlerTheme.scrollBottomInset)
        }
        .background(QuizzlerTheme.terminalBackground.ignoresSafeArea())
        .navigationTitle("Your courses")
        .toolbar(.hidden, for: .navigationBar)
    }

    private func courseCard(for pack: InstalledPack) -> some View {
        let isSelected = catalog.selectedPackKey == pack.id
        let packQuestions = catalog.questions(for: pack)
        let total = packQuestions.count
        let seen = progress.seenIdentities(courseID: pack.courseID, packID: pack.packID).count
        let catalogMap = Dictionary(uniqueKeysWithValues: packQuestions.map { ($0.identity, $0.question) })
        let insights = StudyInsights.derive(
            envelope: progress.envelope,
            catalog: catalogMap,
            pending: progress.unsavedAnswers,
            now: Date()
        )
        let dueCount = insights.due.due

        return Button {
            onSelectCourse(pack.id)
            dismiss()
        } label: {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text(pack.subject)
                        .font(.headline.weight(.semibold))
                        .foregroundStyle(QuizzlerTheme.textPrimary)
                    Spacer()
                    badge(isSelected: isSelected, dueCount: dueCount)
                }

                let fraction = total > 0 ? Double(min(seen, total)) / Double(total) : 0.0
                ProgressView(value: fraction)
                    .progressViewStyle(.linear)
                    .tint(QuizzlerTheme.primaryCyan)

                Text(scheduledReviewEnabled ? "\(seen) of \(total) seen · \(dueCount) due" : "\(seen) of \(total) seen")
                    .font(.footnote)
                    .foregroundStyle(QuizzlerTheme.textMuted)
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
            .overlay(
                RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius)
                    .stroke(isSelected ? QuizzlerTheme.primaryCyan : QuizzlerTheme.border, lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("course-card-\(pack.id)")
        .accessibilityLabel(pack.subject)
        .accessibilityValue(
            scheduledReviewEnabled
                ? "\(seen) of \(total) seen, \(dueCount > 0 ? "\(dueCount) due" : "nothing due")"
                : "\(seen) of \(total) seen"
        )
        .accessibilityAddTraits(isSelected ? [.isSelected] : [])
    }

    @ViewBuilder
    private func badge(isSelected: Bool, dueCount: Int) -> some View {
        if isSelected {
            Text("Studying")
                .font(.caption.weight(.medium))
                .foregroundStyle(QuizzlerTheme.primaryCyan)
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(QuizzlerTheme.primaryCyan.opacity(0.15), in: Capsule())
        } else if scheduledReviewEnabled {
            let label = dueCount > 0 ? "\(dueCount) due" : "Nothing due"
            Text(label)
                .font(.caption.weight(.medium))
                .foregroundStyle(QuizzlerTheme.textMuted)
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(QuizzlerTheme.raisedCard, in: Capsule())
        }
    }
}
