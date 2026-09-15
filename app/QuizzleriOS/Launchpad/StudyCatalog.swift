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

/// Per-device persistence for the pack a learner chose to study.
///
/// This intentionally stays outside the progress repository and CloudKit: a
/// course choice is a local device preference, while answers remain shared
/// progress for their pack-scoped question identities.
protocol StudyCatalogSelectionStore: AnyObject {
    var selectedPackKey: String? { get set }
}

/// The production store. Tests inject an in-memory `StudyCatalogSelectionStore`
/// instead, so they never read or change the user's preferences.
final class UserDefaultsStudyCatalogSelectionStore: StudyCatalogSelectionStore {
    private static let selectedPackKeyPreference = "StudyCatalog.selectedPackKey"

    private let preferences: UserDefaults

    init(preferences: UserDefaults = .standard) {
        self.preferences = preferences
    }

    var selectedPackKey: String? {
        get { preferences.string(forKey: Self.selectedPackKeyPreference) }
        set { preferences.set(newValue, forKey: Self.selectedPackKeyPreference) }
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
    /// Every usable bundled pack, sorted by its durable identity for picker
    /// and list presentation.
    @Published private(set) var availablePacks: [InstalledPack] = []
    /// Packs that were bundled but could not be used. Kept separately from
    /// `state` because a build can carry one good pack and one broken one, and
    /// hiding the broken one is how a course disappears without a trace.
    @Published private(set) var failures: [PackLoadFailure] = []

    private let load: @Sendable () -> (packs: [InstalledPack], failures: [PackLoadFailure], loadError: Error?)
    private let selectionStore: any StudyCatalogSelectionStore

    init(
        load: (@Sendable () -> (packs: [InstalledPack], failures: [PackLoadFailure], loadError: Error?))? = nil,
        selectionStore: any StudyCatalogSelectionStore = UserDefaultsStudyCatalogSelectionStore()
    ) {
        self.load = load ?? {
            do {
                let catalog = try PackCatalog.load()
                return (catalog.packs, catalog.failures, nil)
            } catch {
                return ([], [], error)
            }
        }
        self.selectionStore = selectionStore
    }

    var pack: InstalledPack? {
        if case .ready(let pack, _) = state { return pack }
        return nil
    }

    var questions: [StudyQuestion] {
        if case .ready(_, let questions) = state { return questions }
        return []
    }

    /// The durable identity used by a picker to bind its selected course.
    var selectedPackKey: String? { pack?.id }

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
            apply(outcome)
        }
    }

    /// Chooses an installed pack by its full course/pack identity.
    ///
    /// An unavailable key is deliberately a no-op so a stale picker row or
    /// delayed UI update cannot replace the learner's active course.
    @discardableResult
    func select(packKey: String) -> Bool {
        guard let selected = availablePacks.first(where: { $0.id == packKey }) else {
            return false
        }
        selectionStore.selectedPackKey = selected.id
        state = Self.readyState(for: selected)
        return true
    }

    private func apply(_ outcome: (packs: [InstalledPack], failures: [PackLoadFailure], loadError: Error?)) {
        availablePacks = outcome.packs.sorted { $0.id < $1.id }
        let catalog = PackCatalog(packs: outcome.packs, failures: outcome.failures)
        guard let defaultPack = catalog.primaryPack else {
            state = .unavailable(reason: Self.describeEmpty(outcome))
            return
        }

        let selected = selectionStore.selectedPackKey.flatMap { key in
            availablePacks.first { $0.id == key }
        } ?? defaultPack
        // This persists both an initial local default and replacement for a
        // bundle key that was removed since the prior launch.
        selectionStore.selectedPackKey = selected.id
        state = Self.readyState(for: selected)
    }

    private static func readyState(for pack: InstalledPack) -> State {
        .ready(pack: pack, questions: pack.questions.map { StudyQuestion(pack: pack, question: $0) })
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
