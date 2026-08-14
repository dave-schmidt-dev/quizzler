import Foundation

/// Strict, pack-level validation for values constructed by code as well as
/// values decoded from JSON. The public question initializers are intentionally
/// non-throwing for renderer ergonomics, so a pack boundary must validate them
/// before accepting them.
public extension Question {
    var metadata: QuestionMetadata {
        switch self {
        case .multipleChoice(let question): question.metadata
        case .scenarioMultipleChoice(let question): question.metadata
        case .multipleSelect(let question): question.metadata
        case .trueFalse(let question): question.metadata
        case .matching(let question): question.metadata
        }
    }

    var prompt: String {
        switch self {
        case .multipleChoice(let question): question.prompt
        case .scenarioMultipleChoice(let question): question.prompt
        case .multipleSelect(let question): question.prompt
        case .trueFalse(let question): question.prompt
        case .matching(let question): question.prompt
        }
    }

    var explanation: String {
        switch self {
        case .multipleChoice(let question): question.explanation
        case .scenarioMultipleChoice(let question): question.explanation
        case .multipleSelect(let question): question.explanation
        case .trueFalse(let question): question.explanation
        case .matching(let question): question.explanation
        }
    }

    /// Validates the semantic invariants that Codable decoding and pack
    /// installation require. This is deliberately independent of CloudKit.
    func validateStrict() throws {
        guard !id.isBlankForPack, !prompt.isBlankForPack, !explanation.isBlankForPack else {
            throw QuestionDecodingError.malformedMetadata
        }

        let metadata = metadata
        guard !metadata.topic.isBlankForPack,
              !metadata.examArea.isBlankForPack,
              metadata.tags.allSatisfy({ !$0.isBlankForPack }),
              Set(metadata.tags).count == metadata.tags.count,
              metadata.diagram?.isBlankForPack != true,
              metadata.diagram == nil || !metadata.diagramAlt.isBlankForPack,
              metadata.diagramAlt == nil || metadata.diagram != nil else {
            throw QuestionDecodingError.malformedMetadata
        }

        switch self {
        case .multipleChoice(let question):
            try validateChoice(options: question.options, answer: question.answer)
        case .scenarioMultipleChoice(let question):
            try validateChoice(options: question.options, answer: question.answer)
        case .multipleSelect(let question):
            guard question.options.count >= 2,
                  question.options.allSatisfy({ !$0.isBlankForPack }),
                  Set(question.options).count == question.options.count,
                  question.answers.count >= 2,
                  Set(question.answers).count == question.answers.count,
                  question.answers.allSatisfy({ question.options.indices.contains($0) }) else {
                throw QuestionDecodingError.invalidAnswerIndex
            }
        case .trueFalse:
            break
        case .matching(let question):
            guard !question.leftItems.isEmpty,
                  question.leftItems.allSatisfy({ !$0.isBlankForPack }),
                  Set(question.leftItems).count == question.leftItems.count,
                  !question.rightItems.isEmpty,
                  question.rightItems.allSatisfy({ !$0.isBlankForPack }),
                  Set(question.rightItems).count == question.rightItems.count,
                  question.correctPairs.count == question.leftItems.count,
                  Set(question.correctPairs).count == question.correctPairs.count,
                  question.correctPairs.allSatisfy({ question.rightItems.indices.contains($0) }) else {
                throw QuestionDecodingError.invalidAnswerIndex
            }
        }
    }

    private func validateChoice(options: [String], answer: Int) throws {
        guard options.count >= 2,
              options.allSatisfy({ !$0.isBlankForPack }),
              Set(options).count == options.count,
              options.indices.contains(answer) else {
            throw QuestionDecodingError.invalidAnswerIndex
        }
    }
}

private extension Optional where Wrapped == String {
    var isBlankForPack: Bool { self?.isBlankForPack ?? true }
}

private extension String {
    var isBlankForPack: Bool { trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
}
