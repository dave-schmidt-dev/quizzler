import SwiftUI

/// Whether the Mac's bottom tab bar is on screen. The policy compiles
/// everywhere so the unit tests can table-check it without a Catalyst
/// destination; only the bar's rendering is Catalyst-only.
enum CatalystTabBarPolicy {
    /// Hidden through the whole study flow — question, feedback, and results
    /// use the full viewport — and shown on the three persistent
    /// destinations, the same rule the phone applies to its system tab bar.
    static func isVisible(for state: LaunchpadState) -> Bool {
        switch state {
        case .question, .feedback, .results: false
        case .today, .progress, .settings: true
        }
    }
}

#if targetEnvironment(macCatalyst)
/// The phone's floating bottom tab bar, drawn for the Mac. Catalyst hosts
/// `TabView`'s own tabs in the window toolbar, where a phone-width window
/// collapses them to a titlebar popup, so the Mac hides that bar.
struct CatalystTabBar: View {
    @Binding var selection: LaunchpadState

    var body: some View {
        HStack(spacing: 4) {
            ForEach(LaunchpadState.primaryNavigationStates) { destination in
                let isSelected = selection == destination
                Button {
                    selection = destination
                } label: {
                    VStack(spacing: 2) {
                        Image(systemName: destination.icon)
                            .font(.system(size: 18, weight: .semibold))
                            // Symbols differ in height; a fixed box keeps the labels on one line.
                            .frame(height: 22)
                        Text(destination.title)
                            .font(.caption.weight(.medium))
                    }
                    .foregroundStyle(isSelected ? QuizzlerTheme.primaryCyan : QuizzlerTheme.textPrimary)
                    .frame(minWidth: 96, minHeight: QuizzlerTheme.minimumTouchTarget)
                    .padding(.vertical, 4)
                    .background(isSelected ? QuizzlerTheme.raisedCard : .clear, in: Capsule())
                    .contentShape(Capsule())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(isSelected ? .isSelected : [])
                .accessibilityIdentifier("mac-tab-\(destination.id)")
            }
        }
        .padding(4)
        .background(QuizzlerTheme.elevatedCard, in: Capsule())
        .overlay(Capsule().stroke(QuizzlerTheme.border, lineWidth: 1))
        .frame(maxWidth: .infinity)
        .padding(.bottom, 12)
        .accessibilityIdentifier("mac-tab-bar")
    }
}
#endif
