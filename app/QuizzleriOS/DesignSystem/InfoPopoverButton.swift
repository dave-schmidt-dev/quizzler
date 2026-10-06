import SwiftUI

/// A compact info control that keeps explanatory copy out of the primary
/// content (C17). The button itself stays quiet and the note lives in a
/// popover the learner can open on demand.
struct InfoPopoverButton: View {
    let title: String
    let message: String
    let identifier: String

    @State private var isPresented = false

    var body: some View {
        Button {
            isPresented = true
        } label: {
            Image(systemName: "info.circle")
                .font(.body)
                .foregroundStyle(QuizzlerTheme.textMuted)
                .frame(
                    minWidth: QuizzlerTheme.minimumTouchTarget,
                    minHeight: QuizzlerTheme.minimumTouchTarget
                )
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(title)
        .accessibilityIdentifier(identifier)
        .popover(isPresented: $isPresented) {
            Text(message)
                .font(.footnote)
                .foregroundStyle(QuizzlerTheme.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(16)
                .frame(width: 280, alignment: .leading)
                .accessibilityIdentifier("\(identifier)-message")
                .presentationCompactAdaptation(.popover)
        }
    }
}
