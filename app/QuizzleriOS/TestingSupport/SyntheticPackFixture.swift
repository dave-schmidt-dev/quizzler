import Foundation
import QuizzlerKit

#if DEBUG

/// A DEBUG-only, deterministic question pack for study-preference UI tests.
///
/// The app ships no question content of its own (INV-12), and the packs a
/// build bundles depend on the machine that produced it, so a UI test that
/// must count served questions or start from a fresh course cannot rely on
/// them. When `UITestFixture.syntheticPackEnvironmentKey` asks for N
/// questions, the launchpad studies this pack instead: one course, stable
/// one-based IDs `syn-q001`…, and a first option that is always correct so a
/// test can answer without reading content. The file name and Release source
/// exclusion keep this fixture out of archived apps and release packs.
enum SyntheticStudyPack {
    static let courseID = "synthetic"
    static let packID = "synthetic-core"
    static let subject = "Synthetic"

    /// Stable and zero-padded: `syn-q001`, `syn-q002`, …
    static func questionID(_ index: Int) -> String {
        String(format: "syn-q%03d", index + 1)
    }

    /// `nil` means "no synthetic pack requested"; callers must not infer a
    /// default count from that case.
    static func requestedQuestionCount(environment: [String: String]) -> Int? {
        guard let rawValue = environment[UITestFixture.syntheticPackEnvironmentKey],
              let count = Int(rawValue), count > 0 else { return nil }
        return count
    }

    /// A loader for `StudyCatalogModel(load:selectionStore:)` that serves the
    /// synthetic pack and nothing else. The manifest is built inside the
    /// closure so its validation runs on the same detached task the catalog
    /// schedules the load on, and a refusal is reported through `loadError`
    /// rather than crashing the launch.
    static func makeLoader(
        count: Int
    ) -> (@Sendable () -> (packs: [InstalledPack], failures: [PackLoadFailure], loadError: Error?)) {
        let courseID = Self.courseID
        let packID = Self.packID
        let subject = Self.subject
        let questions = Self.questions(count: count)
        return {
            do {
                let manifest = try PackManifest(
                    packID: packID,
                    subject: subject,
                    title: "Synthetic Core",
                    questions: questions
                )
                let pack = InstalledPack(courseID: courseID, manifest: manifest)
                return (packs: [pack], failures: [], loadError: nil)
            } catch {
                return (packs: [], failures: [], loadError: error)
            }
        }
    }

    /// An in-memory selection store, so a synthetic-pack run never reads or
    /// writes the learner's durable course choice.
    static func makeSelectionStore() -> any StudyCatalogSelectionStore {
        SyntheticStudyPackSelectionStore()
    }

    private static func questions(count: Int) -> [Question] {
        (0..<count).map { index in
            Question.multipleChoice(MultipleChoiceQuestion(
                id: questionID(index),
                metadata: QuestionMetadata(topic: "synthetic", examArea: "synthetic", difficulty: .easy),
                prompt: "Synthetic question \(index + 1): which option is correct?",
                explanation: "Option A is correct by construction.",
                options: ["Option A", "Option B", "Option C", "Option D"],
                answer: 0
            ))
        }
    }
}

private final class SyntheticStudyPackSelectionStore: StudyCatalogSelectionStore {
    var selectedPackKey: String?
}

#endif
