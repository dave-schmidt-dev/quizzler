import Foundation
import SwiftUI
import QuizzlerKit
import UIKit

extension LaunchpadView {
    /// Reserve space in the navigation content's safe area so its scroll view
    /// begins below the pinned controls on iPhone and Mac Catalyst.
    var launchpadHeader: some View {
        VStack(spacing: 0) {
            if state == .question || state == .feedback {
                sessionHeaderRow
            } else {
                HStack(spacing: 8) {
                    headerLeftContext
                        .layoutPriority(0)
                    Spacer(minLength: 8)
                    GlobalProgressStatusControl(progress: progress)
                        .fixedSize(horizontal: true, vertical: false)
                        .layoutPriority(1)
                }
                .padding(.horizontal, QuizzlerTheme.pageGutter)
                .padding(.vertical, 6)
            }
        }
        .background(QuizzlerTheme.terminalBackground)
        .background(alignment: .top) { StatusBarScrim() }
    }

    /// The session header is one pinned row: the exit control, a thin progress
    /// bar, the "N/M" counter, the mode label for scheduled review and retry
    /// sessions, and a sync indicator only while progress is not synced.
    private var sessionHeaderRow: some View {
        HStack(spacing: 12) {
            headerLeftContext

            if let sessionPosition {
                ProgressView(value: sessionPosition.fraction)
                    .progressViewStyle(.linear)
                    .tint(QuizzlerTheme.primaryCyan)
                    .frame(maxWidth: .infinity)

                Text(sessionPosition.counterLabel)
                    .font(.subheadline.weight(.semibold).monospacedDigit())
                    .foregroundStyle(QuizzlerTheme.textMuted)
                    .lineLimit(1)
                    .accessibilityLabel("Question \(sessionPosition.label) in this session")
                    .accessibilityValue(sessionPosition.counterLabel)
                    .accessibilityIdentifier("session-position")
            } else {
                Spacer(minLength: 0)
            }

            if let context = sessionContext {
                Text(context)
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(QuizzlerTheme.primaryCyan)
                    .lineLimit(1)
                    .minimumScaleFactor(0.72)
                    .accessibilityIdentifier("session-context")
            }

            // A synced session shows no sync status; anything else stays visible.
            if progress.persistenceState != .synced {
                GlobalProgressStatusControl(progress: progress)
                    .fixedSize(horizontal: true, vertical: false)
                    .layoutPriority(1)
            }
        }
        .padding(.horizontal, QuizzlerTheme.pageGutter)
        .padding(.vertical, 6)
    }

    @ViewBuilder
    private var headerLeftContext: some View {
        if state == .today {
            if showingCourses {
                Button {
                    showingCourses = false
                } label: {
                    Label("Back to Today", systemImage: "chevron.left")
                        .font(.subheadline.weight(.semibold))
                        .lineLimit(1)
                        .foregroundStyle(QuizzlerTheme.primaryCyan)
                        .modifier(HeaderNavigationCapsule())
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Back to Today")
                .accessibilityIdentifier("courses-back-to-today")
            } else {
                Button {
                    showingCourses = true
                } label: {
                    HStack(spacing: 4) {
                        Text(activeCourseTitle)
                            .font(.subheadline.weight(.semibold))
                            .lineLimit(1)
                            .truncationMode(.tail)
                        Image(systemName: "chevron.right")
                            .font(.caption2.weight(.semibold))
                    }
                    .foregroundStyle(QuizzlerTheme.primaryCyan)
                    .modifier(HeaderNavigationCapsule())
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Change course")
                .accessibilityValue(activeCourseTitle)
                .accessibilityIdentifier("today-change-course")
            }
        } else if state == .progress {
            Text(activeCourseTitle)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(QuizzlerTheme.textPrimary)
                .lineLimit(1)
                .truncationMode(.tail)
        } else if state == .settings {
            Text("Quizzler \(NativeAppVersion.display)")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(QuizzlerTheme.textPrimary)
                .lineLimit(1)
                .truncationMode(.tail)
        } else if state == .question || state == .feedback {
            // An icon keeps the session header to one row. The control stays
            // pinned here rather than scrolling with the question.
            Button(action: endSession) {
                Image(systemName: "xmark")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(QuizzlerTheme.primaryCyan)
                    .frame(minWidth: QuizzlerTheme.minimumTouchTarget)
                    .modifier(HeaderNavigationCapsule())
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            // Escape ends the session (C4): the keyboard's way out.
            .keyboardShortcut(.escape, modifiers: [])
            .accessibilityLabel("Back to Today")
            .accessibilityHint("Ends this study session and returns to Today")
            .accessibilityIdentifier("session-end")
        } else {
            // Results retains the course context after the session ends.
            Text(activeCourseTitle)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(QuizzlerTheme.textMuted)
                .lineLimit(1)
                .truncationMode(.tail)
        }
    }

    private struct HeaderNavigationCapsule: ViewModifier {
        func body(content: Content) -> some View {
            content
                .padding(.horizontal, 10)
                .frame(minHeight: QuizzlerTheme.minimumTouchTarget)
                .background(QuizzlerTheme.raisedCard, in: Capsule())
                .overlay(
                    Capsule()
                        .stroke(QuizzlerTheme.primaryCyan.opacity(0.45), lineWidth: 1)
                )
        }
    }

    private var activeCourseTitle: String {
        switch catalog.state {
        case .loading:
            return "Loading…"
        case .unavailable:
            return catalog.courseTitle
        case .ready(let pack, _):
            return pack.subject
        }
    }

    /// The mode label names only the session kinds a learner can mistake for
    /// course study: scheduled review and retry runs.
    private var sessionContext: String? {
        guard state == .question || state == .feedback,
              let mode = activeSession?.mode else { return nil }
        switch mode {
        case .srs: return "Scheduled review"
        case .retryMissed: return "Retry missed"
        case .normal, .weakAreas: return nil
        }
    }
}

/// An opaque background behind the status bar.
///
/// Scrolling content used to ride up behind the clock and the Dynamic Island
/// and stay legible there, colliding with them. The Today tab hides its
/// navigation bar, so there is no system scroll-edge treatment to inherit.
///
/// The height comes from the key window rather than from a `GeometryReader`:
/// two layout-derived attempts both measured zero here and rendered nothing,
/// and a strip of the wrong height is indistinguishable from no strip at all.
/// It sits behind the pinned header controls so it cannot obscure them.
private struct StatusBarScrim: View {
    private var topInset: CGFloat {
        UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
            .first { $0.isKeyWindow }?
            .safeAreaInsets.top ?? 0
    }

    var body: some View {
        QuizzlerTheme.terminalBackground
            .frame(maxWidth: .infinity)
            .frame(height: topInset)
            .ignoresSafeArea(edges: .top)
            .allowsHitTesting(false)
    }
}
