import Foundation
import QuizzlerKit

/// One question plus the course context a screen needs to show and report it.
///
/// The identity always comes from the pack the question was decoded from. That
/// is the whole point of this type: progress records and issue reports are
/// keyed by course/pack/question, and a constant compiled into the app would
/// attribute real answers to a course the user never studied.
struct StudyQuestion: Identifiable, Equatable, Sendable {
    let identity: QuestionIdentity
    let courseTitle: String
    let question: Question

    init(identity: QuestionIdentity, courseTitle: String, question: Question) {
        self.identity = identity
        self.courseTitle = courseTitle
        self.question = question
    }

    init(pack: InstalledPack, question: Question) {
        self.init(identity: pack.identity(for: question), courseTitle: pack.subject, question: question)
    }

    var id: String { identity.description }
    var qid: String { "\(identity.packID)::\(identity.questionID)" }
    var topic: String {
        switch question {
        case .multipleChoice(let value): value.metadata.topic
        case .scenarioMultipleChoice(let value): value.metadata.topic
        case .multipleSelect(let value): value.metadata.topic
        case .trueFalse(let value): value.metadata.topic
        case .matching(let value): value.metadata.topic
        }
    }
    var prompt: String {
        switch question {
        case .multipleChoice(let value): value.prompt
        case .scenarioMultipleChoice(let value): value.prompt
        case .multipleSelect(let value): value.prompt
        case .trueFalse(let value): value.prompt
        case .matching(let value): value.prompt
        }
    }
    var explanation: String {
        switch question {
        case .multipleChoice(let value): value.explanation
        case .scenarioMultipleChoice(let value): value.explanation
        case .multipleSelect(let value): value.explanation
        case .trueFalse(let value): value.explanation
        case .matching(let value): value.explanation
        }
    }
}

/// Where the next session resumes.
///
/// The position is derived from saved progress rather than held in view state.
/// Holding it in memory meant every relaunch restarted the course at question
/// one and re-served the same few questions forever, which is not what a
/// repository-bound counter means.
enum StudyPosition {
    static func resumeIndex(answered: Int, questionCount: Int) -> Int {
        guard questionCount > 0 else { return 0 }
        // `answered` is never negative in practice; clamping keeps the
        // subscript total rather than trusting that.
        return max(0, answered) % questionCount
    }
}

/// Loads the packs bundled into this build and exposes the one being studied.
///
/// The app ships no question content of its own. `scripts/build_pack_assets.py`
/// writes `question-assets.json` and the pack files into the app bundle during
/// the build, and everything below reads them. When nothing is installed the
/// model says so in a state the UI must render — it never substitutes sample
/// content for the course the user expected.
@MainActor
final class StudyCatalogModel: ObservableObject {
    enum State: Equatable, Sendable {
        case loading
        case ready(pack: InstalledPack, questions: [StudyQuestion])
        case unavailable(reason: String)
    }

    @Published private(set) var state: State = .loading
    /// Packs that were bundled but could not be used. Kept separately from
    /// `state` because a build can carry one good pack and one broken one, and
    /// hiding the broken one is how a course disappears without a trace.
    @Published private(set) var failures: [PackLoadFailure] = []

    private let load: @Sendable () -> (packs: [InstalledPack], failures: [PackLoadFailure], loadError: Error?)

    init(load: (@Sendable () -> (packs: [InstalledPack], failures: [PackLoadFailure], loadError: Error?))? = nil) {
        self.load = load ?? {
            do {
                let catalog = try PackCatalog.load()
                return (catalog.packs, catalog.failures, nil)
            } catch {
                return ([], [], error)
            }
        }
    }

    var pack: InstalledPack? {
        if case .ready(let pack, _) = state { return pack }
        return nil
    }

    var questions: [StudyQuestion] {
        if case .ready(_, let questions) = state { return questions }
        return []
    }

    /// What the Settings screen shows for `Course`.
    var courseTitle: String { pack?.subject ?? "No pack installed" }

    func loadPacks() {
        state = .loading
        let work = load
        Task {
            // Decoding a few hundred questions is small but not free, and the
            // header shows `loading` until it lands (INV-1).
            let outcome = await Task.detached(priority: .userInitiated) { work() }.value
            failures = outcome.failures
            state = Self.resolve(outcome)
        }
    }

    private static func resolve(_ outcome: (packs: [InstalledPack], failures: [PackLoadFailure], loadError: Error?)) -> State {
        let catalog = PackCatalog(packs: outcome.packs, failures: outcome.failures)
        guard let pack = catalog.primaryPack else {
            return .unavailable(reason: describeEmpty(outcome))
        }
        return .ready(pack: pack, questions: pack.questions.map { StudyQuestion(pack: pack, question: $0) })
    }

    private static func describeEmpty(_ outcome: (packs: [InstalledPack], failures: [PackLoadFailure], loadError: Error?)) -> String {
        if let failure = outcome.failures.first {
            let others = outcome.failures.count - 1
            let suffix = others > 0 ? " (and \(others) more)" : ""
            return "\(outcome.failures.count) bundled pack(s) could not be loaded: \(failure.path) — \(failure.reason)\(suffix)"
        }
        if outcome.loadError != nil {
            return "This build carries no question assets. It was produced without the pack bundling step."
        }
        return "No question packs are installed in this build."
    }
}
