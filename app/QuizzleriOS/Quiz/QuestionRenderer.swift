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

/// How one answer row reads once the answer has been checked.
///
/// A wrong answer used to keep the same cyan "selected" treatment as a right
/// one, so the screen never named the correct option and the explanation prose
/// was the only place to find it. These are words, not colour alone, because
/// colour alone is not an answer for a colour-blind learner or VoiceOver.
enum ChoiceMarking: Equatable {
    case none
    case correct
    case yourAnswer

    var caption: String? {
        switch self {
        case .none: nil
        case .correct: "correct"
        case .yourAnswer: "your answer"
        }
    }
}

/// One renderer entry point for all question schema types.
struct QuestionRenderer: View {
    let question: Question
    @Binding var selection: QuestionSelection
    /// `true` only in the feedback phase. While answering, marking a row would
    /// hand the learner the answer before they commit to one.
    var revealCorrect: Bool = false

    var body: some View {
        switch question {
        case .multipleChoice(let question):
            SingleChoiceRenderer(
                options: question.options,
                selection: $selection,
                heading: nil,
                correctIndexes: revealCorrect ? [question.answer] : []
            )
        case .scenarioMultipleChoice(let question):
            SingleChoiceRenderer(
                options: question.options,
                selection: $selection,
                heading: "Scenario response",
                correctIndexes: revealCorrect ? [question.answer] : []
            )
        case .multipleSelect(let question):
            MultipleSelectRenderer(
                options: question.options,
                selection: $selection,
                correctIndexes: revealCorrect ? Set(question.answers) : []
            )
        case .trueFalse(let question):
            TrueFalseRenderer(selection: $selection, correctValue: revealCorrect ? question.answer : nil)
        case .matching(let question):
            MatchingRenderer(leftItems: question.leftItems, rightItems: question.rightItems, selection: $selection)
        }
    }
}

/// The marking for one row: the right answer is named whether or not it was
/// chosen, and a chosen row that is not the right answer is named as the
/// learner's, so the two are never confused with each other.
func choiceMarking(index: Int, selected: Bool, correctIndexes: Set<Int>) -> ChoiceMarking {
    if correctIndexes.contains(index) { return .correct }
    if selected && !correctIndexes.isEmpty { return .yourAnswer }
    return .none
}

private struct SingleChoiceRenderer: View {
    let options: [String]
    @Binding var selection: QuestionSelection
    let heading: String?
    /// Empty while answering; the right answer once feedback is showing.
    let correctIndexes: Set<Int>

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
                    multiple: false,
                    marking: choiceMarking(index: index, selected: selection == .single(index), correctIndexes: correctIndexes)
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
    /// Empty while answering; every right answer once feedback is showing.
    let correctIndexes: Set<Int>

    var body: some View {
        VStack(alignment: .leading, spacing: QuizzlerTheme.stackGap) {
            Text("Select all that apply")
                .font(QuizzlerTheme.metadataFont)
                .foregroundStyle(QuizzlerTheme.textMuted)
                .accessibilityAddTraits(.isHeader)
            ForEach(options.indices, id: \.self) { index in
                let selected = selectedIndexes.contains(index)
                ChoiceButton(
                    title: options[index],
                    selected: selected,
                    multiple: true,
                    marking: choiceMarking(index: index, selected: selected, correctIndexes: correctIndexes)
                ) {
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
    /// `nil` while answering; the right value once feedback is showing.
    let correctValue: Bool?

    var body: some View {
        VStack(spacing: QuizzlerTheme.stackGap) {
            ChoiceButton(
                title: "True",
                selected: selection == .boolean(true),
                multiple: false,
                marking: marking(value: true)
            ) {
                selection = .boolean(true)
            }
            .accessibilityIdentifier("question-true")
            ChoiceButton(
                title: "False",
                selected: selection == .boolean(false),
                multiple: false,
                marking: marking(value: false)
            ) {
                selection = .boolean(false)
            }
            .accessibilityIdentifier("question-false")
        }
    }

    private func marking(value: Bool) -> ChoiceMarking {
        guard let correctValue else { return .none }
        if value == correctValue { return .correct }
        return selection == .boolean(value) ? .yourAnswer : .none
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
    var marking: ChoiceMarking = .none
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
                Spacer(minLength: 8)
                if let caption = marking.caption {
                    Text(caption)
                        .font(QuizzlerTheme.metadataFont)
                        .foregroundStyle(marking == .correct ? QuizzlerTheme.success : QuizzlerTheme.warning)
                        .accessibilityHidden(true)
                }
            }
            .frame(maxWidth: .infinity, minHeight: QuizzlerTheme.minimumTouchTarget, alignment: .leading)
            .padding(.horizontal, 14)
            .background(selected ? QuizzlerTheme.elevatedCard.opacity(0.95) : QuizzlerTheme.elevatedCard.opacity(0.65), in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
            .overlay(RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius).stroke(selected ? QuizzlerTheme.primaryCyan : .clear, lineWidth: 1))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(title)
        .accessibilityValue(accessibilityValue)
        .accessibilityAddTraits(selected ? [.isSelected] : [])
    }

    /// VoiceOver hears the marking as well as the selection, because the
    /// caption beside the title is hidden from it to avoid a second element.
    private var accessibilityValue: String {
        let state = selected ? "Selected" : "Not selected"
        guard let caption = marking.caption else { return state }
        return "\(state), \(caption)"
    }
}
