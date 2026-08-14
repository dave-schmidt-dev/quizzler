import SwiftUI
import QuizzlerKit

/// The transient answer a learner has selected for the current question.
enum QuestionSelection: Equatable, Sendable {
    case none
    case single(Int)
    case multiple(Set<Int>)
    case boolean(Bool)
    case matching([Int])

    var isEmpty: Bool {
        switch self {
        case .none: true
        case .single, .boolean: false
        case .multiple(let values): values.isEmpty
        case .matching(let values): values.isEmpty || values.contains(where: { $0 < 0 })
        }
    }
}

/// One renderer entry point for all question schema types.
struct QuestionRenderer: View {
    let question: Question
    @Binding var selection: QuestionSelection

    var body: some View {
        switch question {
        case .multipleChoice(let question):
            SingleChoiceRenderer(options: question.options, selection: $selection, heading: nil)
        case .scenarioMultipleChoice(let question):
            SingleChoiceRenderer(options: question.options, selection: $selection, heading: "Scenario response")
        case .multipleSelect(let question):
            MultipleSelectRenderer(options: question.options, selection: $selection)
        case .trueFalse:
            TrueFalseRenderer(selection: $selection)
        case .matching(let question):
            MatchingRenderer(leftItems: question.leftItems, rightItems: question.rightItems, selection: $selection)
        }
    }
}

private struct SingleChoiceRenderer: View {
    let options: [String]
    @Binding var selection: QuestionSelection
    let heading: String?

    var body: some View {
        VStack(spacing: QuizzlerTheme.stackGap) {
            if let heading {
                Text(heading)
                    .font(QuizzlerTheme.metadataFont)
                    .foregroundStyle(QuizzlerTheme.textMuted)
                    .accessibilityAddTraits(.isHeader)
            }
            ForEach(options.indices, id: \.self) { index in
                ChoiceButton(
                    title: options[index],
                    selected: selection == .single(index),
                    multiple: false
                ) {
                    selection = .single(index)
                }
                .accessibilityIdentifier("question-choice-\(index)")
            }
        }
    }
}

private struct MultipleSelectRenderer: View {
    let options: [String]
    @Binding var selection: QuestionSelection

    var body: some View {
        VStack(alignment: .leading, spacing: QuizzlerTheme.stackGap) {
            Text("Select all that apply")
                .font(QuizzlerTheme.metadataFont)
                .foregroundStyle(QuizzlerTheme.textMuted)
                .accessibilityAddTraits(.isHeader)
            ForEach(options.indices, id: \.self) { index in
                let selected = selectedIndexes.contains(index)
                ChoiceButton(title: options[index], selected: selected, multiple: true) {
                    var next = selectedIndexes
                    if selected { next.remove(index) } else { next.insert(index) }
                    selection = .multiple(next)
                }
                .accessibilityIdentifier("question-choice-\(index)")
            }
        }
    }

    private var selectedIndexes: Set<Int> {
        if case .multiple(let values) = selection { return values }
        return []
    }
}

private struct TrueFalseRenderer: View {
    @Binding var selection: QuestionSelection

    var body: some View {
        VStack(spacing: QuizzlerTheme.stackGap) {
            ChoiceButton(title: "True", selected: selection == .boolean(true), multiple: false) {
                selection = .boolean(true)
            }
            .accessibilityIdentifier("question-true")
            ChoiceButton(title: "False", selected: selection == .boolean(false), multiple: false) {
                selection = .boolean(false)
            }
            .accessibilityIdentifier("question-false")
        }
    }
}

private struct MatchingRenderer: View {
    let leftItems: [String]
    let rightItems: [String]
    @Binding var selection: QuestionSelection

    var body: some View {
        VStack(alignment: .leading, spacing: QuizzlerTheme.stackGap) {
            Text("Match each item")
                .font(QuizzlerTheme.metadataFont)
                .foregroundStyle(QuizzlerTheme.textMuted)
                .accessibilityAddTraits(.isHeader)
            ForEach(leftItems.indices, id: \.self) { index in
                HStack(alignment: .center, spacing: QuizzlerTheme.stackGap) {
                    Text(leftItems[index])
                        .font(QuizzlerTheme.readableFont)
                        .foregroundStyle(QuizzlerTheme.textPrimary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Menu {
                        ForEach(rightItems.indices, id: \.self) { rightIndex in
                            Button(rightItems[rightIndex]) {
                                var next = selectedIndexes
                                if next.count < leftItems.count { next += Array(repeating: -1, count: leftItems.count - next.count) }
                                next[index] = rightIndex
                                selection = .matching(next)
                            }
                        }
                    } label: {
                        HStack {
                            Text(selectedTitle(for: index))
                                .lineLimit(2)
                            Image(systemName: "chevron.up.chevron.down")
                                .font(.caption)
                        }
                        .foregroundStyle(QuizzlerTheme.textPrimary)
                        .frame(maxWidth: .infinity, minHeight: QuizzlerTheme.minimumTouchTarget, alignment: .leading)
                        .padding(.horizontal, 12)
                        .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
                    }
                    .accessibilityLabel("Match \(leftItems[index])")
                    .accessibilityHint("Choose the matching item")
                    .accessibilityIdentifier("question-match-\(index)")
                    .frame(maxWidth: .infinity)
                }
            }
        }
    }

    private var selectedIndexes: [Int] {
        if case .matching(let values) = selection { return values }
        return Array(repeating: -1, count: leftItems.count)
    }

    private func selectedTitle(for index: Int) -> String {
        let rightIndex = selectedIndexes.indices.contains(index) ? selectedIndexes[index] : -1
        return rightItems.indices.contains(rightIndex) ? rightItems[rightIndex] : "Choose an answer"
    }
}

private struct ChoiceButton: View {
    let title: String
    let selected: Bool
    let multiple: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(alignment: .center, spacing: 12) {
                Image(systemName: selected ? (multiple ? "checkmark.square.fill" : "circle.inset.filled") : (multiple ? "square" : "circle"))
                    .foregroundStyle(selected ? QuizzlerTheme.primaryCyan : QuizzlerTheme.textMuted)
                    .accessibilityHidden(true)
                Text(title)
                    .font(QuizzlerTheme.readableFont)
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                    .multilineTextAlignment(.leading)
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, minHeight: QuizzlerTheme.minimumTouchTarget, alignment: .leading)
            .padding(.horizontal, 14)
            .background(selected ? QuizzlerTheme.elevatedCard.opacity(0.95) : QuizzlerTheme.elevatedCard.opacity(0.65), in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
            .overlay(RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius).stroke(selected ? QuizzlerTheme.primaryCyan : .clear, lineWidth: 1))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(title)
        .accessibilityValue(selected ? "Selected" : "Not selected")
        .accessibilityAddTraits(selected ? [.isSelected] : [])
    }
}
