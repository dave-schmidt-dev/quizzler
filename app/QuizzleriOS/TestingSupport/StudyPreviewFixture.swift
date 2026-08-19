import Foundation
import QuizzlerKit

#if DEBUG

/// Deterministic question data for previews, unit tests, and snapshot
/// baselines. It covers all five schema types so the renderers can be
/// exercised without an installed pack.
///
/// This is **not** study content and must never reach a shipped screen. The
/// file name matches the Release `EXCLUDED_SOURCE_FILE_NAMES` pattern, so it
/// is not compiled into an archive at all; the `#if DEBUG` is a second lock in
/// case that setting is ever loosened. An earlier build did ship these three
/// questions as the entire course, which is what
/// `docs/WALKTHROUGH-2026-08-18.md` finding 1 recorded.
enum SeededStudyData {
    static let courseID = "security-plus"
    static let packID = "sy0-701"
    static let courseTitle = "Security+"
    static let metadata = QuestionMetadata(topic: "Threats and mitigations", examArea: "Architecture", difficulty: .medium)

    static let questions: [StudyQuestion] = [
        StudyQuestion(
            identity: QuestionIdentity(courseID: courseID, packID: packID, questionID: "q0042"),
            courseTitle: courseTitle,
            question: .multipleChoice(MultipleChoiceQuestion(
                id: "q0042", metadata: metadata,
                prompt: "Which control most directly limits lateral movement after an endpoint is compromised?",
                explanation: "Network segmentation limits which systems a compromised endpoint can reach.",
                options: ["Network segmentation", "Data masking", "Full-disk encryption", "Password rotation"], answer: 0
            ))
        ),
        StudyQuestion(
            identity: QuestionIdentity(courseID: courseID, packID: packID, questionID: "q0043"),
            courseTitle: courseTitle,
            question: .scenarioMultipleChoice(ScenarioMultipleChoiceQuestion(
                id: "q0043", metadata: metadata,
                prompt: "A team needs a second factor that resists phishing. Which choice is best?",
                explanation: "A hardware security key provides phishing-resistant authentication.",
                options: ["Hardware security key", "SMS code", "Security question", "Email link"], answer: 0
            ))
        ),
        StudyQuestion(
            identity: QuestionIdentity(courseID: courseID, packID: packID, questionID: "q0044"),
            courseTitle: courseTitle,
            question: .multipleSelect(MultipleSelectQuestion(
                id: "q0044", metadata: metadata,
                prompt: "Which two practices reduce the impact of exposed credentials?",
                explanation: "Least privilege and short credential lifetimes reduce what an exposed credential can do.",
                options: ["Least privilege", "Short credential lifetimes", "Shared admin accounts", "Permanent tokens"], answers: [0, 1]
            ))
        ),
        StudyQuestion(
            identity: QuestionIdentity(courseID: courseID, packID: packID, questionID: "preview-true-false"),
            courseTitle: courseTitle,
            question: .trueFalse(TrueFalseQuestion(
                id: "preview-true-false", metadata: metadata,
                prompt: "Encryption by itself controls which internal hosts an attacker can reach.",
                explanation: "False. Segmentation, not encryption, constrains lateral movement.", answer: false
            ))
        ),
        StudyQuestion(
            identity: QuestionIdentity(courseID: courseID, packID: packID, questionID: "preview-matching"),
            courseTitle: courseTitle,
            question: .matching(MatchingQuestion(
                id: "preview-matching", metadata: metadata,
                prompt: "Match each control to its primary security goal.",
                explanation: "Each control is mapped to its primary goal in the preview data.",
                leftItems: ["Segmentation", "Encryption", "Least privilege"],
                rightItems: ["Limit reach", "Protect confidentiality", "Limit permissions"],
                correctPairs: [0, 1, 2]
            ))
        )
    ]
}

#endif
