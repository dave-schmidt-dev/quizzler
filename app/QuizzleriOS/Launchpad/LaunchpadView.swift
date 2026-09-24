import Foundation
import SwiftUI
import QuizzlerKit
import UIKit

/// The six top-level states in Launchpad A.
enum LaunchpadState: String, CaseIterable, Identifiable {
    case today
    case question
    case feedback
    case results
    case progress
    case settings

    var id: String { rawValue }

    var title: String {
        rawValue.capitalized
    }

    var icon: String {
        switch self {
        case .today: "sun.max"
        case .question: "questionmark.circle"
        case .feedback: "checkmark.message"
        case .results: "chart.bar"
        case .progress: "chart.line.uptrend.xyaxis"
        case .settings: "gearshape"
        }
    }

    /// Launchpad keeps the study flow in one state machine while exposing
    /// only the three persistent destinations from the locked design.
    static let primaryNavigationStates: [LaunchpadState] = [.today, .progress, .settings]
}

@MainActor
final class LaunchpadProgressModel: ObservableObject {
    enum PersistenceState: Equatable {
        case loading
        case local
        case saving
        case syncing
        case synced
        case syncPending
        case accountChanged
        case saveFailed
    }

    @Published private(set) var aggregate = AggregateSnapshot()
    @Published private(set) var unsavedAnswers: [SessionAnswer] = []
    @Published private(set) var persistenceState: PersistenceState = .loading
    @Published private(set) var isReadyForStudy = false

    private let repository: any LaunchpadProgressRepository
    private let beforeSave: @Sendable () async -> Void
    /// Published so study surfaces can derive insights from the same
    /// envelope the counters come from, rather than keeping a second copy
    /// that can drift from it.
    @Published private(set) var envelope: ProgressEnvelope?
    @Published private(set) var maximumLevelError: String?
    private var saveIsInFlight = false
    private var syncIsInFlight = false
    private var maximumLevelChangeIsInFlight = false
    private var progressStreamTask: Task<Void, Never>?
    private var syncStatusStreamTask: Task<Void, Never>?

    init(repository: any LaunchpadProgressRepository, beforeSave: @escaping @Sendable () async -> Void = {}) {
        self.repository = repository
        self.beforeSave = beforeSave
    }

    deinit {
        progressStreamTask?.cancel()
        syncStatusStreamTask?.cancel()
    }

    var answered: Int { aggregate.answered + unsavedAnswers.count }
    var correct: Int { aggregate.correct + unsavedAnswers.filter(\.correct).count }
    var maximumLeitnerLevel: Int { envelope?.maximumLeitnerLevel ?? LeitnerSchedule.defaultMaximumLevel }

    func aggregate(courseID: String, packID: String) -> AggregateSnapshot {
        let stored = envelope?.mastery.reduce(into: AggregateSnapshot()) { result, mastery in
            guard mastery.identity.courseID == courseID,
                  mastery.identity.packID == packID else { return }
            result.answered += mastery.answered
            result.correct += mastery.correct
        } ?? AggregateSnapshot()
        let pending = unsavedAnswers.filter {
            $0.identity.courseID == courseID && $0.identity.packID == packID
        }
        return AggregateSnapshot(
            answered: stored.answered + pending.count,
            correct: stored.correct + pending.filter(\.correct).count
        )
    }

    /// Returns every identity in that pack with a mastery entry (answered > 0)
    /// or a pending unsaved answer.
    func seenIdentities(courseID: String, packID: String) -> Set<QuestionIdentity> {
        var seen = Set<QuestionIdentity>()
        if let mastery = envelope?.mastery {
            for entry in mastery {
                if entry.identity.courseID == courseID,
                   entry.identity.packID == packID,
                   entry.answered > 0 {
                    seen.insert(entry.identity)
                }
            }
        }
        for answer in unsavedAnswers {
            if answer.identity.courseID == courseID,
               answer.identity.packID == packID {
                seen.insert(answer.identity)
            }
        }
        return seen
    }

    var persistenceStatus: String {
        Self.persistenceStatus(for: persistenceState)
    }

    static func persistenceStatus(for state: PersistenceState) -> String {
        switch state {
        case .loading: "loading local progress"
        case .local: "local progress saved"
        case .saving: "saving progress locally"
        case .syncing: "syncing progress"
        case .synced: "last sync succeeded"
        case .syncPending: "progress saved here · sync pending"
        case .accountChanged: "iCloud account changed · local history kept safe"
        case .saveFailed: "local save failed · retry required"
        }
    }

    func load() {
        persistenceState = .loading
        isReadyForStudy = false
        startProgressObservation()
        startSyncStatusObservation()
        Task {
            do {
                guard repository.syncMode == .cloudKit else {
                    let snapshot = try await repository.snapshot()
                    if snapshot.schemaVersion == 1 {
                        _ = try await repository.setMaximumLeitnerLevel(LeitnerSchedule.defaultMaximumLevel)
                    }
                    let readySnapshot = try await repository.snapshot()
                    guard readySnapshot.schemaVersion == ProgressEnvelope.currentSchemaVersion else {
                        throw ProgressRepositoryError.corruptState
                    }
                    apply(readySnapshot)
                    isReadyForStudy = true
                    persistenceState = .local
                    return
                }
                startSynchronization()
            } catch {
                isReadyForStudy = false
                persistenceState = .saveFailed
            }
        }
    }

    func record(_ answer: SessionAnswer) {
        unsavedAnswers.append(answer)
    }

    /// Records an answer and starts its durable save before feedback is shown.
    /// The in-flight guard makes a later Next-question save a no-op for this
    /// same answer while still allowing answers added afterward to drain.
    func recordAndSave(_ answer: SessionAnswer) {
        record(answer)
        saveCurrentSession()
    }

    func saveCurrentSession() {
        guard !unsavedAnswers.isEmpty else {
            if persistenceState == .saveFailed, !saveIsInFlight, !syncIsInFlight {
                load()
            } else if persistenceState == .syncPending {
                refreshCloudProgress()
            }
            return
        }
        // The current save or CloudKit fetch/send will finish first. It then
        // drains the answers accumulated while it was in flight, preserving
        // order without two tasks removing the same prefix.
        guard !saveIsInFlight, !syncIsInFlight else { return }
        persistNextBatch()
    }

    /// Re-fetches shared progress when the app returns to the foreground.
    /// The existing in-flight guards keep this from competing with a save or
    /// another synchronization already started by the launch lifecycle.
    func synchronizeOnForeground() {
        refreshCloudProgress()
    }

    /// Writes the selected cap through the progress repository so it is
    /// durable and shared with the learner's other devices.
    func setMaximumLeitnerLevel(_ maximum: Int) {
        guard (1...7).contains(maximum), isReadyForStudy,
              maximum != self.maximumLeitnerLevel,
              !saveIsInFlight, !syncIsInFlight, !maximumLevelChangeIsInFlight else { return }
        maximumLevelError = nil
        maximumLevelChangeIsInFlight = true
        let previousPersistenceState = persistenceState
        persistenceState = repository.syncMode == .cloudKit ? .syncing : .saving
        Task {
            do {
                let updated = try await repository.setMaximumLeitnerLevel(maximum)
                apply(updated)
                if repository.syncMode == .cloudKit {
                    try await repository.synchronize()
                    apply(try await repository.snapshot())
                    persistenceState = .synced
                } else {
                    apply(try await repository.snapshot())
                    persistenceState = .local
                }
            } catch {
                if let localSnapshot = try? await repository.snapshot() {
                    apply(localSnapshot)
                }
                if let cloudError = error as? CloudProgressRepositoryError,
                   case let .maximumLevelChangeTooLarge(affected, limit) = cloudError {
                    maximumLevelError = "This change would adjust \(affected) questions. The current sync limit is \(limit) questions per change."
                    persistenceState = previousPersistenceState
                } else {
                    persistenceState = repository.syncMode == .cloudKit ? .syncPending : .saveFailed
                }
            }
            maximumLevelChangeIsInFlight = false
        }
    }

    private func persistNextBatch() {
        guard !saveIsInFlight, !syncIsInFlight, !unsavedAnswers.isEmpty else { return }
        let batch = Array(unsavedAnswers)
        saveIsInFlight = true
        persistenceState = .saving
        Task {
            do {
                await beforeSave()
                _ = try await repository.save(SessionDetail(answers: batch))
                guard unsavedAnswers.count >= batch.count else {
                    saveIsInFlight = false
                    persistenceState = .saveFailed
                    return
                }
                unsavedAnswers.removeFirst(batch.count)
                apply(try await repository.snapshot())
                saveIsInFlight = false
                if !unsavedAnswers.isEmpty {
                    persistNextBatch()
                } else if repository.syncMode == .cloudKit {
                    startSynchronization()
                } else {
                    persistenceState = .local
                }
            } catch {
                saveIsInFlight = false
                persistenceState = .saveFailed
            }
        }
    }

    private func refreshCloudProgress() {
        guard repository.syncMode == .cloudKit, !saveIsInFlight else { return }
        startSynchronization()
    }

    private func startSynchronization() {
        guard repository.syncMode == .cloudKit, !saveIsInFlight, !syncIsInFlight else { return }
        syncIsInFlight = true
        persistenceState = .syncing
        Task {
            do {
                try await repository.synchronize()
                var authoritativeSnapshot = try await repository.snapshot()
                if authoritativeSnapshot.schemaVersion == 1 {
                    _ = try await repository.setMaximumLeitnerLevel(LeitnerSchedule.defaultMaximumLevel)
                    try await repository.synchronize()
                    authoritativeSnapshot = try await repository.snapshot()
                }
                guard authoritativeSnapshot.schemaVersion == ProgressEnvelope.currentSchemaVersion else {
                    throw ProgressRepositoryError.corruptState
                }
                apply(authoritativeSnapshot)
                isReadyForStudy = true
                syncIsInFlight = false
                if unsavedAnswers.isEmpty {
                    persistenceState = .synced
                } else {
                    persistNextBatch()
                }
            } catch {
                syncIsInFlight = false
                if !isReadyForStudy {
                    isReadyForStudy = false
                }
                if let error = error as? CloudProgressRepositoryError,
                   error == .accountIsolationRequired {
                    persistenceState = .accountChanged
                } else if unsavedAnswers.isEmpty {
                    // The CloudKit checkpoint is already durable. A failed
                    // transfer must not be presented as a failed local save.
                    persistenceState = .syncPending
                } else {
                    persistNextBatch()
                }
            }
        }
    }

    private func apply(_ envelope: ProgressEnvelope) {
        self.envelope = envelope
        aggregate = envelope.aggregate
    }

    /// Keeps visible counters current while a cloud-backed app remains open.
    /// The repository owns actor serialization and deduplication; this model
    /// has one cancellable consumer so reloads cannot leave stale listeners.
    private func startProgressObservation() {
        guard repository.syncMode == .cloudKit else { return }
        progressStreamTask?.cancel()
        let repository = self.repository
        progressStreamTask = Task { [weak self] in
            let stream = await repository.progressSnapshots()
            for await envelope in stream {
                guard !Task.isCancelled else { return }
                self?.apply(envelope)
            }
        }
    }

    /// Account isolation is a safety boundary, not a retryable network lapse.
    /// Surface it as soon as CloudKit reports the account transition so the
    /// user is not directed to retry an operation that cannot succeed.
    private func startSyncStatusObservation() {
        guard repository.syncMode == .cloudKit else { return }
        syncStatusStreamTask?.cancel()
        let repository = self.repository
        syncStatusStreamTask = Task { [weak self] in
            let stream = await repository.syncStatusEvents()
            for await status in stream {
                guard !Task.isCancelled else { return }
                guard status.reason == .accountChanged
                        || status.state == .accountIsolationRequired else { continue }
                self?.persistenceState = .accountChanged
            }
        }
    }
}

enum LaunchpadSyncMode: Sendable, Equatable {
    case local
    case cloudKit
}

protocol LaunchpadProgressRepository: Sendable {
    var syncMode: LaunchpadSyncMode { get }
    func snapshot() async throws -> ProgressEnvelope
    func progressSnapshots() async -> AsyncStream<ProgressEnvelope>
    func save(_ session: SessionDetail) async throws -> ProgressOperation
    func queueIssue(_ issue: QuestionIssue) async throws -> QuestionIssue
    func synchronize() async throws
    func setMaximumLeitnerLevel(_ maximum: Int) async throws -> ProgressEnvelope
    func reviewHistory(for identity: QuestionIdentity) async throws -> QuestionReviewHistory
    func syncStatusEvents() async -> AsyncStream<SyncStatusEvent>
}

extension LaunchpadProgressRepository {
    /// Persists a report before making one best-effort CloudKit send. A report
    /// must remain available for retry even when the network phase fails.
    func queueIssueAndScheduleSync(_ issue: QuestionIssue) async throws -> QuestionIssue {
        let queuedIssue = try await queueIssue(issue)
        guard syncMode == .cloudKit else { return queuedIssue }

        Task { [self] in
            try? await synchronize()
        }
        return queuedIssue
    }

    func syncStatusEvents() async -> AsyncStream<SyncStatusEvent> {
        AsyncStream { continuation in continuation.finish() }
    }

    /// Generic repositories may not retain immutable review events. Returning
    /// an incomplete empty history keeps that limitation explicit in the UI.
    func reviewHistory(for identity: QuestionIdentity) async throws -> QuestionReviewHistory {
        .incomplete([])
    }

    func setMaximumLeitnerLevel(_ maximum: Int) async throws -> ProgressEnvelope {
        throw ProgressRepositoryError.invalidOperation
    }
}

enum LeitnerSchedule {
    static let intervalDays = [1, 3, 7, 14, 30, 60, 120]
    static let defaultMaximumLevel = 5

    static func intervalDays(for level: Int) -> Int? {
        guard intervalDays.indices.contains(level - 1) else { return nil }
        return intervalDays[level - 1]
    }

    static func intervalLabel(for level: Int) -> String {
        guard let days = intervalDays(for: level) else { return "Unknown interval" }
        return "\(days) \(days == 1 ? "day" : "days")"
    }

    static func nextLevel(current: Int?, correct: Bool, maximum: Int) -> Int {
        let prior = min(maximum, current ?? 1)
        return correct ? min(maximum, prior + 1) : max(1, prior - 2)
    }
}

/// The shared aggregate belongs to every signed-in device. Resume position is
/// deliberately local to this device and selected pack, so answers completed
/// elsewhere can improve visible mastery without skipping this review queue.
/// How many questions a normal session serves.
///
/// Sessions were fixed at ten. The length is a study decision, not a build
/// constant, so it lives in Settings; `wholePack` is a sentinel rather than a
/// number because the pack size changes with the selected course.
enum StudySessionLength {
    static let key = "quizzler.session-length.v1"

    /// The sentinel stored for "every question in the pack".
    static let wholePack = 0

    /// Offered in Settings, in the order they appear there.
    static let options = [10, 20, 40, wholePack]

    static let `default` = 10

    static func label(_ value: Int) -> String {
        value == wholePack ? "Whole pack" : "\(value) questions"
    }

    /// The Today selection is intentionally temporary. It can refine the next
    /// session without rewriting the learner's Settings default.
    static func effective(stored: Int, nextSessionOverride: Int?) -> Int {
        if let nextSessionOverride, options.contains(nextSessionOverride) {
            return nextSessionOverride
        }
        return options.contains(stored) ? stored : `default`
    }

    static func maximumLabel(_ value: Int) -> String {
        value == wholePack ? "Whole pack" : "Up to \(value) questions"
    }

    /// The number of questions to request. A stored value that is not one of
    /// the offered options falls back to the default rather than trusting it,
    /// so a corrupted or hand-edited default cannot request a nonsense limit.
    static func limit(stored: Int, packQuestionCount: Int) -> Int {
        guard packQuestionCount > 0 else { return 0 }
        guard options.contains(stored) else { return min(`default`, packQuestionCount) }
        if stored == wholePack { return packQuestionCount }
        return min(stored, packQuestionCount)
    }

    static func limit(stored: Int, nextSessionOverride: Int?, candidateCount: Int) -> Int {
        limit(
            stored: effective(stored: stored, nextSessionOverride: nextSessionOverride),
            packQuestionCount: candidateCount
        )
    }
}

enum StudyScheduledReview {
    static let key = "quizzler.scheduled-review.v1"
    static let `default` = true
}

enum NativeAppVersion {
    static var display: String {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String
        switch (version?.trimmingCharacters(in: .whitespacesAndNewlines), build?.trimmingCharacters(in: .whitespacesAndNewlines)) {
        case let (.some(v), .some(b)) where !v.isEmpty && !b.isEmpty:
            return "\(v) (\(b))"
        case let (.some(v), _) where !v.isEmpty:
            return v
        case let (_, .some(b)) where !b.isEmpty:
            return "(\(b))"
        default:
            return "Unavailable"
        }
    }
}

enum StudyResumePosition {
    private static let keyPrefix = "quizzler.study-resume-position.v1"

    static func index(
        courseID: String,
        packID: String,
        questionCount: Int,
        defaults: UserDefaults = .standard
    ) -> Int {
        guard questionCount > 0 else { return 0 }
        let value = defaults.object(forKey: key(courseID: courseID, packID: packID)) as? Int ?? 0
        return ((value % questionCount) + questionCount) % questionCount
    }

    static func store(
        _ index: Int,
        courseID: String,
        packID: String,
        questionCount: Int,
        defaults: UserDefaults = .standard
    ) {
        guard questionCount > 0 else { return }
        defaults.set(
            ((index % questionCount) + questionCount) % questionCount,
            forKey: key(courseID: courseID, packID: packID)
        )
    }

    private static func key(courseID: String, packID: String) -> String {
        "\(keyPrefix).\(courseID).\(packID)"
    }
}

extension ProgressRepository: LaunchpadProgressRepository {
    nonisolated var syncMode: LaunchpadSyncMode { .local }

    func progressSnapshots() async -> AsyncStream<ProgressEnvelope> {
        AsyncStream { continuation in continuation.finish() }
    }

    func save(_ session: SessionDetail) async throws -> ProgressOperation {
        try await save(session, operationID: nil, now: Date())
    }

    func setMaximumLeitnerLevel(_ maximum: Int) async throws -> ProgressEnvelope {
        _ = try await setMaximumLeitnerLevel(maximum, operationID: nil, now: Date())
        return try await snapshot()
    }

    func synchronize() async throws {
        // The legacy local repository has no network phase. Keeping this a
        // protocol-level no-op makes test fixtures and offline previews use
        // the same launchpad lifecycle without claiming shared progress.
    }
}

/// Determines the primary study action and time estimate on the Today screen.
enum TodayRecommendation: Equatable, Sendable {
    case review(batch: Int, due: Int)
    case learn(batch: Int, unseen: Int)
    case caughtUp(batch: Int)

    init(due: Int, unseen: Int, sessionLimit: Int, scheduledReviewEnabled: Bool = true) {
        if scheduledReviewEnabled, due > 0 {
            self = .review(batch: min(due, sessionLimit), due: due)
        } else if unseen > 0 {
            self = .learn(batch: min(unseen, sessionLimit), unseen: unseen)
        } else {
            self = .caughtUp(batch: sessionLimit)
        }
    }

    var batch: Int {
        switch self {
        case .review(let batch, _): batch
        case .learn(let batch, _): batch
        case .caughtUp(let batch): batch
        }
    }

    var minutes: Int {
        max(1, Int(ceil(Double(batch) * 0.75)))
    }

    var title: String {
        switch self {
        case .review(let batch, _):
            "Scheduled review: \(batch) \(batch == 1 ? "question" : "questions")"
        case .learn:
            "Ready to learn"
        case .caughtUp:
            "Ready to practice"
        }
    }

    var detail: String {
        let minuteWord = minutes == 1 ? "minute" : "minutes"
        switch self {
        case .review(let batch, let due):
            let backlog = due > batch ? " · \(due) due overall" : ""
            return "Spaced repetition\(backlog) · about \(minutes) \(minuteWord)"
        case .learn(let batch, _):
            let questionWord = batch == 1 ? "question" : "questions"
            return "Learn \(batch) new \(questionWord) · about \(minutes) \(minuteWord)"
        case .caughtUp:
            return "About \(minutes) \(minuteWord)"
        }
    }

    var buttonTitle: String {
        switch self {
        case .review:
            "Start review"
        case .learn:
            "Start learning"
        case .caughtUp:
            "Keep practicing"
        }
    }

    var isReview: Bool {
        if case .review = self { return true }
        return false
    }

    var isLearn: Bool {
        if case .learn = self { return true }
        return false
    }

    var isCaughtUp: Bool {
        if case .caughtUp = self { return true }
        return false
    }
}

/// The questions this session will serve, and how far through them we are.
/// `nil` between sessions, when the position follows from saved progress instead.
/// It is pinned for the duration of a session so recording an answer cannot
/// swap the question out from under the Feedback screen.
struct ActiveSession {
    let mode: SelectionMode
    let questions: [StudyQuestion]
    var position: Int
    var answers: [SessionAnswer]
    let newIdentities: Set<QuestionIdentity>

    init(
        mode: SelectionMode,
        questions: [StudyQuestion],
        position: Int = 0,
        answers: [SessionAnswer] = [],
        newIdentities: Set<QuestionIdentity> = []
    ) {
        self.mode = mode
        self.questions = questions
        self.position = position
        self.answers = answers
        self.newIdentities = newIdentities
    }

    /// Returns a copy with `position` incremented by one, or `nil` when the
    /// session has no remaining questions. Used by finish and skip so both
    /// paths advance the position through the same expression.
    func advanced() -> ActiveSession? {
        let next = position + 1
        guard next < questions.count else { return nil }
        var copy = self
        copy.position = next
        return copy
    }
}

struct LaunchpadView: View {
    @Environment(\.scenePhase) private var scenePhase
    @State private var state: LaunchpadState = .today
    /// The questions this session will serve, and how far through them we are.
    /// `nil` between sessions, when the position follows from saved progress.
    @State private var activeSession: ActiveSession?
    @State private var selection: QuestionSelection = .none
    @State private var scheduledReviewStates: [QuestionIdentity: SRSState] = [:]
    private let repository: any LaunchpadProgressRepository
    @StateObject private var progress: LaunchpadProgressModel
    @StateObject private var catalog: StudyCatalogModel
#if targetEnvironment(macCatalyst)
    @StateObject private var issueInbox = IssueInboxModel()
#endif

    /// The durable default chosen in Settings.
    @AppStorage(StudySessionLength.key) private var storedSessionLength = StudySessionLength.default
    @AppStorage(StudyScheduledReview.key) private var scheduledReviewEnabled = StudyScheduledReview.default
    /// A Today choice applies once. Settings remains the durable source of
    /// truth, so changing this value cannot silently alter future sessions.
    @State private var nextSessionLengthOverride: Int?
    @State private var showingCourses = false

    init(repository: any LaunchpadProgressRepository, catalog: StudyCatalogModel = StudyCatalogModel()) {
        self.repository = repository
        _progress = StateObject(wrappedValue: LaunchpadProgressModel(repository: repository))
        _catalog = StateObject(wrappedValue: catalog)
#if targetEnvironment(macCatalyst)
        _issueInbox = StateObject(wrappedValue: IssueInboxModel())
#endif
    }

    /// `nil` until a pack is installed and decoded. Every study screen is
    /// gated on this rather than falling back to built-in content: an app with
    /// no packs must look empty, not look like a very short course.
    private var currentQuestion: StudyQuestion? {
        let questions = catalog.questions
        guard !questions.isEmpty else { return nil }
        if let session = activeSession {
            guard session.position < session.questions.count else { return nil }
            return session.questions[session.position]
        }
        return questions[resumeIndex(count: questions.count)]
    }

    private func resumeIndex(count: Int) -> Int {
        guard let pack = catalog.pack else { return 0 }
        return StudyResumePosition.index(
            courseID: pack.courseID,
            packID: pack.packID,
            questionCount: count
        )
    }

    private var currentInsights: StudyInsights {
        let catalogMap = Dictionary(
            uniqueKeysWithValues: catalog.questions.map { ($0.identity, $0.question) }
        )
        return StudyInsights.derive(
            envelope: progress.envelope,
            catalog: catalogMap,
            pending: progress.unsavedAnswers,
            now: Date()
        )
    }

    private var effectiveSessionLength: Int {
        StudySessionLength.effective(
            stored: storedSessionLength,
            nextSessionOverride: nextSessionLengthOverride
        )
    }

    private func sessionLimit(candidateCount: Int) -> Int {
        StudySessionLength.limit(
            stored: storedSessionLength,
            nextSessionOverride: nextSessionLengthOverride,
            candidateCount: candidateCount
        )
    }

    /// Clear the one-time Today selection only after a valid session has been
    /// created. A failed or empty launch leaves the learner's next choice intact.
    private func consumeNextSessionLengthOverride() {
        nextSessionLengthOverride = nil
    }

    private var selectedNavigationState: LaunchpadState {
        switch state {
        case .question, .feedback, .results: .today
        default: state
        }
    }

    private var tabSelection: Binding<LaunchpadState> {
        Binding(
            get: { selectedNavigationState },
            set: { newState in state = newState }
        )
    }

    var body: some View {
        TabView(selection: tabSelection) {
            NavigationStack {
                VStack(spacing: 0) {
                    studyContent
                }
                .background(QuizzlerTheme.terminalBackground.ignoresSafeArea())
                .navigationTitle("Today")
                .toolbar(.hidden, for: .navigationBar)
                .safeAreaInset(edge: .top, spacing: 0) {
                    launchpadHeader
                }
                .navigationDestination(isPresented: $showingCourses) {
                    CoursesView(
                        catalog: catalog,
                        progress: progress,
                        scheduledReviewEnabled: scheduledReviewEnabled,
                        onSelectCourse: { key in
                            selectCourse(key)
                            showingCourses = false
                        }
                    )
                    .safeAreaInset(edge: .top, spacing: 0) {
                        launchpadHeader
                    }
                }
#if !targetEnvironment(macCatalyst)
                // Hide the tab bar while the learner is inside a session so
                // the question and feedback screens use the full viewport.
                .toolbar(
                    state == .question || state == .feedback || state == .results ? .hidden : .visible,
                    for: .tabBar
                )
#endif
            }
            .cappedTabContentWidth()
            .tabItem {
                Label(LaunchpadState.today.title, systemImage: LaunchpadState.today.icon)
            }
            .tag(LaunchpadState.today)

            NavigationStack {
                StudyProgressView(
                    insights: currentInsights,
                    scheduledReviewEnabled: scheduledReviewEnabled,
                    persistenceState: progress.persistenceState,
                    onRetrySync: progress.saveCurrentSession
                )
                .safeAreaInset(edge: .top, spacing: 0) {
                    launchpadHeader
                }
                .toolbar(.hidden, for: .navigationBar)
            }
            .cappedTabContentWidth()
            .tabItem {
                Label(LaunchpadState.progress.title, systemImage: LaunchpadState.progress.icon)
            }
            .tag(LaunchpadState.progress)

            NavigationStack {
#if targetEnvironment(macCatalyst)
                SettingsView(
                    catalog: catalog,
                    progress: progress,
                    issueInbox: issueInbox
                )
                .navigationTitle("Settings")
                .safeAreaInset(edge: .top, spacing: 0) {
                    launchpadHeader
                }
                .toolbar(.hidden, for: .navigationBar)
#else
                SettingsView(
                    catalog: catalog,
                    progress: progress
                )
                .navigationTitle("Settings")
                .safeAreaInset(edge: .top, spacing: 0) {
                    launchpadHeader
                }
                .toolbar(.hidden, for: .navigationBar)
#endif
            }
            .cappedTabContentWidth()
            .tabItem {
                Label(LaunchpadState.settings.title, systemImage: LaunchpadState.settings.icon)
            }
            .tag(LaunchpadState.settings)
        }
        .environmentObject(progress)
#if targetEnvironment(macCatalyst)
        .safeAreaInset(edge: .bottom, spacing: 0) {
            // Same rule as the phone: no tab bar inside a session.
            if !(state == .question || state == .feedback || state == .results) {
                CatalystTabBar(selection: tabSelection)
            }
        }
        .background(CatalystWindowShaper())
#endif
        .preferredColorScheme(.dark)
        .tint(QuizzlerTheme.primaryCyan)
        .task {
            progress.load()
            catalog.loadPacks()
#if targetEnvironment(macCatalyst)
            issueInbox.refresh()
#endif
        }
        .onChange(of: scenePhase) { _, newPhase in
            guard newPhase == .active else { return }
            progress.synchronizeOnForeground()
#if targetEnvironment(macCatalyst)
            issueInbox.refresh()
#endif
        }
    }

    /// Reserve space in the navigation content's safe area so its scroll view
    /// begins below the pinned controls on iPhone and Mac Catalyst.
    private var launchpadHeader: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                headerLeftContext
                    .layoutPriority(0)
                Spacer(minLength: 8)
                if let context = sessionContext {
                    Text(context)
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(QuizzlerTheme.primaryCyan)
                        .lineLimit(1)
                        .minimumScaleFactor(0.72)
                        .layoutPriority(1)
                        .accessibilityIdentifier("session-context")
                }
                Spacer(minLength: 8)
                GlobalProgressStatusControl(progress: progress)
                    .fixedSize(horizontal: true, vertical: false)
                    .layoutPriority(1)
            }
            .padding(.horizontal, QuizzlerTheme.pageGutter)
            .padding(.vertical, 6)

            if (state == .question || state == .feedback), let sessionPosition {
                HStack(spacing: 12) {
                    ProgressView(value: sessionPosition.fraction)
                        .progressViewStyle(.linear)
                        .tint(QuizzlerTheme.primaryCyan)
                        .frame(maxWidth: .infinity)

                    Text(sessionPosition.displayLabel)
                        .font(.subheadline.weight(.semibold).monospacedDigit())
                        .foregroundStyle(QuizzlerTheme.textMuted)
                        .lineLimit(1)
                        .accessibilityLabel("Question \(sessionPosition.label) in this session")
                        .accessibilityValue(sessionPosition.displayLabel)
                        .accessibilityIdentifier("session-position")
                }
                .padding(.horizontal, QuizzlerTheme.pageGutter)
                .padding(.top, 2)
                .padding(.bottom, 6)
            }
        }
        .background(QuizzlerTheme.terminalBackground)
        .background(alignment: .top) { StatusBarScrim() }
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
            Button(action: endSession) {
                ViewThatFits(in: .horizontal) {
                    Label("Back to Today", systemImage: "chevron.left")
                    Label("Today", systemImage: "chevron.left")
                }
                .font(.subheadline.weight(.semibold))
                .lineLimit(1)
                .foregroundStyle(QuizzlerTheme.primaryCyan)
                .modifier(HeaderNavigationCapsule())
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
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

    private var sessionContext: String? {
        guard state == .question || state == .feedback,
              let mode = activeSession?.mode else { return nil }
        switch mode {
        case .srs: return "Scheduled review"
        case .retryMissed: return "Retry missed"
        case .normal: return "Course study"
        case .weakAreas: return "Weak areas"
        }
    }

    @ViewBuilder private var studyContent: some View {
        if !progress.isReadyForStudy {
            StudyPreparationView(state: progress.persistenceState)
        } else {
            switch catalog.state {
            case .loading:
                PackLoadingView()
            case .unavailable(let reason):
                NoPackInstalledView(reason: reason, onProgress: { state = .progress })
            case .ready(let pack, let questions):
                if let question = currentQuestion {
                    readyContent(pack: pack, questions: questions, question: question)
                } else {
                    NoPackInstalledView(reason: "The installed pack contains no questions.", onProgress: { state = .progress })
                }
            }
        }
    }

    @ViewBuilder private func readyContent(pack: InstalledPack, questions: [StudyQuestion], question: StudyQuestion) -> some View {
        let seenCount = progress.seenIdentities(courseID: pack.courseID, packID: pack.packID).count
        let unseenCount = max(0, questions.count - seenCount)
        switch state {
        case .today:
            TodayView(
                questionNumber: resumeIndex(count: questions.count) + 1,
                questionCount: questions.count,
                unseenCount: unseenCount,
                dueCount: currentInsights.due.due,
                scheduledReviewEnabled: scheduledReviewEnabled,
                sessionLength: effectiveSessionLength,
                missedCount: currentInsights.recentMisses.count,
                maximumLeitnerLevel: progress.maximumLeitnerLevel,
                onStart: startSession,
                onStartDueReview: startDueReview,
                onStartRetryMissed: startRetryMissed,
                onChooseNextSessionLength: { nextSessionLengthOverride = $0 }
            )
        case .question:
            QuestionShellView(
                studyQuestion: question,
                phase: .question,
                repository: repository,
                progressEnvelope: progress.envelope,
                maximumLeitnerLevel: progress.maximumLeitnerLevel,
                sessionMode: activeSession?.mode ?? .normal,
                reviewStateAtSessionStart: scheduledReviewStates[question.identity],
                answerTimestamp: activeSession?.answers.last(where: { $0.identity == question.identity })?.answeredAt,
                selection: $selection,
                onCheck: checkAnswer,
                onFinish: {},
                onSkip: skipQuestion
            )
        case .feedback:
            QuestionShellView(
                studyQuestion: question,
                phase: .feedback(correct: isCorrect(question)),
                repository: repository,
                progressEnvelope: progress.envelope,
                maximumLeitnerLevel: progress.maximumLeitnerLevel,
                sessionMode: activeSession?.mode ?? .normal,
                reviewStateAtSessionStart: scheduledReviewStates[question.identity],
                answerTimestamp: activeSession?.answers.last(where: { $0.identity == question.identity })?.answeredAt,
                selection: $selection,
                onCheck: { _ in },
                onFinish: finishQuestion,
                onSkip: skipQuestion
            )
        case .results:
            if let session = activeSession {
                SessionSummaryView(
                    session: session,
                    courseTitle: pack.subject,
                    saving: progress.persistenceState == .saving,
                    saveFailed: progress.persistenceState == .saveFailed,
                    syncPending: progress.persistenceState == .syncPending,
                    accountChanged: progress.persistenceState == .accountChanged,
                    onRetrySave: progress.saveCurrentSession,
                    onRetryMissed: startRetryMissedFromSession,
                    onNext: startSession,
                    onDone: { state = .today }
                )
            } else {
                // No session snapshot means the state machine reached .results
                // without completing a plan — fall back to Today rather than a
                // blank screen, which would look like a crash to the learner.
                TodayView(
                    questionNumber: resumeIndex(count: questions.count) + 1,
                    questionCount: questions.count,
                    unseenCount: unseenCount,
                    dueCount: currentInsights.due.due,
                    scheduledReviewEnabled: scheduledReviewEnabled,
                    sessionLength: effectiveSessionLength,
                    missedCount: currentInsights.recentMisses.count,
                    maximumLeitnerLevel: progress.maximumLeitnerLevel,
                    onStart: startSession,
                    onStartDueReview: startDueReview,
                    onStartRetryMissed: startRetryMissed,
                    onChooseNextSessionLength: { nextSessionLengthOverride = $0 }
                )
            }
        case .progress, .settings:
            EmptyView()
        }
    }

    /// `nil` when no session is running, so a resumed single question is not
    /// captioned with a run length it is not part of.
    private var sessionPosition: SessionPosition? {
        guard let session = activeSession, session.position < session.questions.count else { return nil }
        return SessionPosition(index: session.position, count: session.questions.count)
    }

    private func isCorrect(_ question: StudyQuestion) -> Bool {
        QuestionShellView.correctAnswer(for: question.question, selection: selection)
    }

    private func startSession() {
        let questions = catalog.questions
        guard !questions.isEmpty else { return }
        let catalogMap = Dictionary(uniqueKeysWithValues: questions.map { ($0.identity, $0.question) })
        let limit = sessionLimit(candidateCount: questions.count)
        guard let request = try? SelectionRequest(mode: .normal, limit: limit) else { return }
        let plan = StudySessionPlan.build(
            request: request,
            envelope: progress.envelope,
            catalog: catalogMap,
            packOrder: questions.map(\.identity),
            resumeIndex: resumeIndex(count: questions.count),
            now: Date()
        )
        // Resolve plan identities back to StudyQuestion objects so the session
        // can serve them directly without re-indexing into the pack each step.
        let sessionQuestions = plan.questions.compactMap { identity in
            questions.first { $0.identity == identity }
        }
        guard !sessionQuestions.isEmpty else { return }
        selection = .none
        activeSession = ActiveSession(
            mode: .normal,
            questions: sessionQuestions,
            position: 0,
            answers: [],
            newIdentities: newIdentities(for: sessionQuestions)
        )
        scheduledReviewStates = [:]
        consumeNextSessionLengthOverride()
        state = .question
    }

    private func startDueReview() {
        guard scheduledReviewEnabled else { return }
        let questions = catalog.questions
        let limit = sessionLimit(candidateCount: questions.count)
        startModeSession(mode: .srs, count: min(currentInsights.due.due, limit))
    }

    private func startRetryMissed() {
        startModeSession(
            mode: .retryMissed,
            count: sessionLimit(candidateCount: currentInsights.recentMisses.count)
        )
    }

    /// Starts a new retryMissed session seeded from the just-finished session's
    /// wrong answers, so the learner re-drills exactly what they missed without
    /// mixing in new SRS-due questions.
    private func startRetryMissedFromSession() {
        guard let session = activeSession else { return }
        let wrongIdentities = session.answers.filter { !$0.correct }.map(\.identity)
        guard !wrongIdentities.isEmpty else { return }
        let questions = catalog.questions
        let sessionQuestions = wrongIdentities.prefix(sessionLimit(candidateCount: wrongIdentities.count)).compactMap { identity in
            questions.first { $0.identity == identity }
        }
        guard !sessionQuestions.isEmpty else { return }
        selection = .none
        activeSession = ActiveSession(
            mode: .retryMissed,
            questions: sessionQuestions,
            position: 0,
            answers: [],
            newIdentities: newIdentities(for: sessionQuestions)
        )
        scheduledReviewStates = [:]
        consumeNextSessionLengthOverride()
        state = .question
    }

    private func startModeSession(mode: SelectionMode, count: Int) {
        let questions = catalog.questions
        guard !questions.isEmpty, count > 0 else { return }
        let catalogMap = Dictionary(uniqueKeysWithValues: questions.map { ($0.identity, $0.question) })
        guard let request = try? SelectionRequest(mode: mode, limit: count) else { return }
        let plan = StudySessionPlan.build(
            request: request,
            envelope: progress.envelope,
            catalog: catalogMap,
            packOrder: questions.map(\.identity),
            resumeIndex: resumeIndex(count: questions.count),
            now: Date()
        )
        let sessionQuestions = plan.questions.compactMap { identity in
            questions.first { $0.identity == identity }
        }
        guard !sessionQuestions.isEmpty else { return }
        selection = .none
        activeSession = ActiveSession(
            mode: mode,
            questions: sessionQuestions,
            position: 0,
            answers: [],
            newIdentities: newIdentities(for: sessionQuestions)
        )
        scheduledReviewStates = mode == .srs
            ? Dictionary(uniqueKeysWithValues: sessionQuestions.compactMap { question in
                progress.envelope?.srs.first(where: { $0.identity == question.identity }).map { (question.identity, $0.state) }
            })
            : [:]
        consumeNextSessionLengthOverride()
        state = .question
    }

    private func newIdentities(for questions: [StudyQuestion]) -> Set<QuestionIdentity> {
        guard !questions.isEmpty else { return [] }
        var seen = Set<QuestionIdentity>()
        var queriedPacks = Set<String>()
        for question in questions {
            let key = "\(question.identity.courseID)::\(question.identity.packID)"
            if queriedPacks.insert(key).inserted {
                seen.formUnion(progress.seenIdentities(courseID: question.identity.courseID, packID: question.identity.packID))
            }
        }
        return Set(questions.map(\.identity).filter { !seen.contains($0) })
    }

    private func selectCourse(_ packKey: String) {
        guard catalog.select(packKey: packKey) else { return }
        // A session belongs to the old pack. Returning to Today is clearer
        // than carrying a numeric position into a newly selected course.
        activeSession = nil
        scheduledReviewStates = [:]
        selection = .none
        state = .today
    }

    private func checkAnswer(_: Bool) {
        // Answering on tap can deliver a second selection change before the
        // Feedback screen replaces the question; only the first one counts.
        guard state == .question, let question = currentQuestion, var session = activeSession else { return }
        let correct = isCorrect(question)
        let answer = SessionAnswer(identity: question.identity, correct: correct, answeredAt: Date())
        session.answers.append(answer)
        activeSession = session
        progress.recordAndSave(answer)
        state = .feedback
    }

    private func finishQuestion() {
        // Save while the answered item remains pinned. The pin prevents an async
        // save from swapping the feedback item before this transition completes.
        guard let session = activeSession else { return }
        progress.saveCurrentSession()
        advancePackResumePosition(for: session)
        selection = .none
        if let next = session.advanced() {
            activeSession = next
            state = .question
        } else {
            // Session exhausted — show the summary with the completed session snapshot.
            state = .results
        }
    }

    private func skipQuestion() {
        // Skip records nothing. For .normal sessions the pack-level resume
        // position still advances past the skipped question so a relaunch does
        // not re-serve it. Then move to the next question or results.
        guard let session = activeSession else { return }
        advancePackResumePosition(for: session)
        selection = .none
        if let next = session.advanced() {
            activeSession = next
            state = .question
        } else {
            state = .results
        }
    }

    private func endSession() {
        // When ending from the feedback screen the answer is already recorded.
        // Save it and advance the pack position so the resumption point is
        // consistent with having finished the question (C6).
        if case .feedback = state, let session = activeSession {
            progress.saveCurrentSession()
            advancePackResumePosition(for: session)
        }
        activeSession = nil
        selection = .none
        state = .today
    }

    /// Advances the pack-level resume position past the current session
    /// question so a relaunch starts at the next unreviewed question.
    /// Only written for `.normal` sessions; curated (SRS, retry) modes leave
    /// the position untouched because they serve a non-contiguous subset.
    private func advancePackResumePosition(for session: ActiveSession) {
        guard session.mode == .normal, let pack = catalog.pack else { return }
        let allQuestions = catalog.questions
        let questionCount = allQuestions.count
        guard questionCount > 0 else { return }
        // Compute the next index from the pack rather than the session so the
        // plan's starting offset is respected regardless of where it began.
        let currentPackIndex = allQuestions.firstIndex(where: {
            $0.identity == session.questions[session.position].identity
        }) ?? 0
        let nextPackIndex = (currentPackIndex + 1) % questionCount
        StudyResumePosition.store(
            nextPackIndex,
            courseID: pack.courseID,
            packID: pack.packID,
            questionCount: questionCount
        )
    }
}

/// Shown while the bundled packs are being decoded (INV-1: the wait is visible).
private struct PackLoadingView: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            eyebrow("Today")
            Label("Loading question packs…", systemImage: "arrow.triangle.2.circlepath")
                .font(.headline)
                .foregroundStyle(QuizzlerTheme.textMuted)
                .accessibilityLabel("Loading question packs")
            Spacer()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(QuizzlerTheme.pageGutter)
        .background(QuizzlerTheme.terminalBackground)
        .accessibilityIdentifier("pack-loading")
    }
}

/// The honest empty state.
///
/// This build carries no questions of its own, so when nothing is installed
/// there is nothing to study and the screen says exactly that. Substituting
/// built-in sample questions here would make an empty install look like a
/// working course, which is the defect this screen replaced.
private struct NoPackInstalledView: View {
    let reason: String
    let onProgress: () -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                eyebrow("Today")
                Text("No questions available")
                    .font(.largeTitle.weight(.bold))
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                Text(reason)
                    .font(.subheadline)
                    .foregroundStyle(QuizzlerTheme.danger)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("no-pack-reason")
                Text("Question packs are added when the app is built. Install a pack and build again.")
                    .font(.subheadline)
                    .foregroundStyle(QuizzlerTheme.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
                Button("View progress", action: onProgress)
                    .buttonStyle(.bordered)
                    .tint(QuizzlerTheme.primaryCyan)
                    .frame(maxWidth: .infinity, minHeight: 44)
            }
            .padding(QuizzlerTheme.pageGutter)
            .padding(.bottom, QuizzlerTheme.scrollBottomInset)
        }
        .background(QuizzlerTheme.terminalBackground)
        .accessibilityIdentifier("no-pack-installed")
    }
}

/// The first screen a tester sees. Every number on it comes from the installed
/// pack or the progress repository.
struct TodayView: View {
    let questionNumber: Int
    let questionCount: Int
    let unseenCount: Int
    let dueCount: Int
    let scheduledReviewEnabled: Bool
    let sessionLength: Int
    let missedCount: Int
    let maximumLeitnerLevel: Int
    let onStart: () -> Void
    let onStartDueReview: () -> Void
    let onStartRetryMissed: () -> Void
    let onChooseNextSessionLength: (Int) -> Void
    @State private var reviewExplanationPresented = false

    private var recommendation: TodayRecommendation {
        let limit = StudySessionLength.limit(stored: sessionLength, packQuestionCount: questionCount)
        return TodayRecommendation(
            due: dueCount,
            unseen: unseenCount,
            sessionLimit: limit,
            scheduledReviewEnabled: scheduledReviewEnabled
        )
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                heroCard

                quietListCard
            }
            .padding(QuizzlerTheme.pageGutter)
            .padding(.top, 16)
            .padding(.bottom, QuizzlerTheme.scrollBottomInset)
        }
        .background(QuizzlerTheme.terminalBackground)
        .navigationTitle("Today")
        .toolbar(.hidden, for: .navigationBar)
    }

    private var heroCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 4) {
                Text(recommendation.title)
                    .font(.title2.weight(.bold))
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                Text(recommendation.detail)
                    .font(.subheadline)
                    .foregroundStyle(QuizzlerTheme.textMuted)
            }

            Button(action: {
                switch recommendation {
                case .review:
                    onStartDueReview()
                case .learn, .caughtUp:
                    onStart()
                }
            }) {
                Text(recommendation.buttonTitle)
                    .font(.body.weight(.semibold))
                    .frame(maxWidth: .infinity, minHeight: 48)
            }
            .buttonStyle(.borderedProminent)
            .tint(QuizzlerTheme.primaryCyan)
            .foregroundStyle(.black)
            .accessibilityLabel(recommendation.buttonTitle)
            .accessibilityIdentifier("today-hero-start")

            Button("How reviews work") {
                reviewExplanationPresented = true
            }
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(QuizzlerTheme.primaryCyan)
            .accessibilityIdentifier("today-how-reviews-work")
        }
        .padding(18)
        .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
        .overlay(
            RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius)
                .stroke(QuizzlerTheme.primaryCyan.opacity(0.3), lineWidth: 1)
        )
        .sheet(isPresented: $reviewExplanationPresented) {
            ScheduledReviewsExplanationView(maximumLevel: maximumLeitnerLevel)
        }
    }

    private var quietListCard: some View {
        VStack(spacing: 10) {
            learnNewRow
            retryMissedRow
            sessionLengthRow
        }
    }

    private var learnNewRow: some View {
        Button(action: onStart) {
            HStack {
                Text("Learn new questions")
                    .font(.body)
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                Spacer()
                Text("\(unseenCount)")
                    .font(.body.monospacedDigit())
                    .foregroundStyle(QuizzlerTheme.textMuted)
                Image(systemName: "arrow.right.circle.fill")
                    .font(.body)
                    .foregroundStyle(QuizzlerTheme.primaryCyan)
            }
            .padding(.horizontal, 16)
            .frame(maxWidth: .infinity, minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Learn new questions")
        .accessibilityIdentifier("today-learn-new")
        .accessibilityValue("Question \(questionNumber) of \(questionCount)")
        .todayActionSurface()
    }

    private var retryMissedRow: some View {
        Button(action: onStartRetryMissed) {
            HStack {
                Text("Retry missed")
                    .font(.body)
                    .foregroundStyle(missedCount > 0 ? QuizzlerTheme.textPrimary : QuizzlerTheme.textMuted)
                Spacer()
                Text(missedBatchCount == missedCount ? "\(missedCount)" : "\(missedBatchCount) of \(missedCount)")
                    .font(.body.monospacedDigit())
                    .foregroundStyle(QuizzlerTheme.textMuted)
                Image(systemName: "arrow.right.circle.fill")
                    .font(.body)
                    .foregroundStyle(missedCount > 0 ? QuizzlerTheme.primaryCyan : QuizzlerTheme.textMuted)
            }
            .padding(.horizontal, 16)
            .frame(maxWidth: .infinity, minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(missedCount == 0)
        .accessibilityLabel("Retry missed")
        .accessibilityIdentifier("today-retry-missed")
        .accessibilityValue(
            missedCount == 0
                ? "No missed questions"
                : "Next session: \(missedBatchCount) of \(missedCount) missed questions"
        )
        .opacity(missedCount == 0 ? 0.55 : 1)
        .todayActionSurface()
    }

    private var missedBatchCount: Int {
        StudySessionLength.limit(stored: sessionLength, packQuestionCount: missedCount)
    }

    private var sessionLengthRow: some View {
        Menu {
            ForEach(StudySessionLength.options, id: \.self) { option in
                Button {
                    onChooseNextSessionLength(option)
                } label: {
                    HStack {
                        Text(StudySessionLength.label(option))
                        if sessionLength == option {
                            Image(systemName: "checkmark")
                        }
                    }
                }
            }
        } label: {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Session length")
                        .font(.body)
                        .foregroundStyle(QuizzlerTheme.textPrimary)
                    Text("Next session only")
                        .font(.caption)
                        .foregroundStyle(QuizzlerTheme.textMuted)
                }
                Spacer()
                Text(StudySessionLength.maximumLabel(sessionLength))
                    .font(.body)
                    .foregroundStyle(QuizzlerTheme.textMuted)
                Image(systemName: "chevron.up.chevron.down")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(QuizzlerTheme.primaryCyan)
            }
            .padding(.horizontal, 16)
            .frame(maxWidth: .infinity, minHeight: 44)
            .contentShape(Rectangle())
        }
        .accessibilityLabel("Session length")
        .accessibilityValue(StudySessionLength.maximumLabel(sessionLength))
        .accessibilityHint("Applies only to the next session")
        .accessibilityIdentifier("today-session-length")
        .todayActionSurface()
    }

}

private struct StudyPreparationView: View {
    let state: LaunchpadProgressModel.PersistenceState

    private var canRetry: Bool {
        state == .syncPending || state == .saveFailed
    }

    var body: some View {
        VStack(spacing: 14) {
            if canRetry {
                Image(systemName: "exclamationmark.icloud")
                    .font(.largeTitle)
                    .foregroundStyle(QuizzlerTheme.warning)
                Text("Progress setup needs attention")
                    .font(.headline)
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                Text("Scheduled review settings must finish syncing before study can start. Tap the status badge above to retry.")
                    .font(.subheadline)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(QuizzlerTheme.textMuted)
            } else if state == .accountChanged {
                Image(systemName: "person.crop.circle.badge.exclamationmark")
                    .font(.largeTitle)
                    .foregroundStyle(QuizzlerTheme.warning)
                Text("iCloud account changed")
                    .font(.headline)
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                Text("Return to the account that owns this progress to finish preparing reviews.")
                    .font(.subheadline)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(QuizzlerTheme.textMuted)
            } else {
                ProgressView()
                    .tint(QuizzlerTheme.primaryCyan)
                Text("Preparing your reviews…")
                    .font(.subheadline)
                    .foregroundStyle(QuizzlerTheme.textMuted)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(QuizzlerTheme.pageGutter)
        .background(QuizzlerTheme.terminalBackground)
        .accessibilityIdentifier("study-preparation")
    }
}

struct ScheduledReviewsExplanationView: View {
    let maximumLevel: Int
    @Environment(\.dismiss) private var dismiss

    private var intervals: String {
        (1...max(1, min(maximumLevel, 7)))
            .compactMap(LeitnerSchedule.intervalDays(for:))
            .map { "\($0) \($0 == 1 ? "day" : "days")" }
            .joined(separator: ", ")
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("Each answered question has a Leitner level from 1 to your maximum of \(maximumLevel). The review interval for each level is \(intervals).")
                    Text("A correct answer moves up one level, stopping at your maximum. A missed answer moves down two levels, stopping at level 1.")
                    Text("A question is due when its next review date arrives. Today offers due questions first. Session length is a maximum, so a session with fewer due questions contains fewer questions.")
                    Text("Turning off Offer scheduled reviews hides that suggestion on Today. Your levels, review dates, and history remain saved.")
                    Text("Lowering your maximum brings longer review dates forward and records the change in each affected question’s history.")
                    Link(destination: URL(string: "https://en.wikipedia.org/wiki/Spaced_repetition")!) {
                        Label("Spaced repetition on Wikipedia", systemImage: "arrow.up.right.square")
                    }
                    .accessibilityIdentifier("scheduled-reviews-wikipedia-link")
                }
                .font(.body)
                .foregroundStyle(QuizzlerTheme.textPrimary)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(QuizzlerTheme.pageGutter)
            }
            .background(QuizzlerTheme.terminalBackground)
            .navigationTitle("How reviews work")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                        .accessibilityIdentifier("scheduled-reviews-done")
                }
            }
        }
        .preferredColorScheme(.dark)
    }
}

/// Compact, live persistence status reserved above every top-level study
/// surface. Successful sync and failure states are controls so their recovery
/// and refresh actions remain reachable without relying on a stale,
/// screen-local status string.
struct GlobalProgressStatusControl: View {
    @ObservedObject var progress: LaunchpadProgressModel

    private var state: LaunchpadProgressModel.PersistenceState {
        progress.persistenceState
    }

    static let textColor = QuizzlerTheme.textPrimary

    static func compactLabel(for state: LaunchpadProgressModel.PersistenceState) -> String {
        switch state {
        case .loading: "Loading"
        case .local: "Saved"
        case .saving: "Saving"
        case .syncing: "Syncing"
        case .synced: "Synced"
        case .syncPending: "Pending sync"
        case .accountChanged: "Account changed"
        case .saveFailed: "Retry save"
        }
    }

    static func icon(for state: LaunchpadProgressModel.PersistenceState) -> String {
        switch state {
        case .synced: "checkmark.icloud.fill"
        case .saving, .syncing: "arrow.triangle.2.circlepath"
        case .syncPending, .accountChanged, .saveFailed: "exclamationmark.icloud.fill"
        case .loading: "circle.dotted"
        case .local: "internaldrive"
        }
    }

    static func iconColor(for state: LaunchpadProgressModel.PersistenceState) -> Color {
        switch state {
        case .synced: QuizzlerTheme.success
        case .syncPending, .accountChanged, .saveFailed: QuizzlerTheme.danger
        case .loading, .local, .saving, .syncing: QuizzlerTheme.textMuted
        }
    }

    private var isRetryable: Bool {
        state == .syncPending || state == .saveFailed
    }

    private var icon: String {
        Self.icon(for: state)
    }

    private var iconColor: Color {
        Self.iconColor(for: state)
    }

    var body: some View {
        if state == .synced {
            Button(action: progress.synchronizeOnForeground) {
                statusLabel
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("global-progress-status")
            .accessibilityLabel(Self.compactLabel(for: state))
            .accessibilityHint("Checks for updates")
        } else if isRetryable {
            Button(action: progress.saveCurrentSession) {
                statusLabel
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("global-progress-status")
            .accessibilityLabel(progress.persistenceStatus)
            .accessibilityHint(state == .saveFailed ? "Retries saving recorded progress" : "Retries iCloud synchronization")
        } else {
            statusLabel
                .accessibilityIdentifier("global-progress-status")
                .accessibilityLabel(progress.persistenceStatus)
        }
    }

    private var statusLabel: some View {
        Label {
            Text(Self.compactLabel(for: state))
                .foregroundStyle(Self.textColor)
        } icon: {
            Image(systemName: icon)
                .foregroundStyle(iconColor)
        }
        .font(.caption.weight(.semibold))
        .lineLimit(1)
        .fixedSize(horizontal: true, vertical: false)
        .padding(.horizontal, 10)
        .frame(minHeight: QuizzlerTheme.minimumTouchTarget)
        .background(QuizzlerTheme.raisedCard, in: Capsule())
        .overlay(
            Capsule().stroke(iconColor.opacity(0.45), lineWidth: 1)
        )
        .accessibilityElement(children: .ignore)
    }
}

/// Lists all installed course packs and lets the learner switch courses.
struct CoursesView: View {
    @ObservedObject var catalog: StudyCatalogModel
    @ObservedObject var progress: LaunchpadProgressModel
    let scheduledReviewEnabled: Bool
    let onSelectCourse: (String) -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Your courses")
                    .font(.title2.weight(.bold))
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .accessibilityAddTraits(.isHeader)
                    .accessibilityIdentifier("courses-heading")

                ForEach(catalog.availablePacks) { pack in
                    courseCard(for: pack)
                }
            }
            .padding(QuizzlerTheme.pageGutter)
            .padding(.bottom, QuizzlerTheme.scrollBottomInset)
        }
        .background(QuizzlerTheme.terminalBackground.ignoresSafeArea())
        .navigationTitle("Your courses")
        .toolbar(.hidden, for: .navigationBar)
    }

    private func courseCard(for pack: InstalledPack) -> some View {
        let isSelected = catalog.selectedPackKey == pack.id
        let packQuestions = catalog.questions(for: pack)
        let total = packQuestions.count
        let seen = progress.seenIdentities(courseID: pack.courseID, packID: pack.packID).count
        let catalogMap = Dictionary(uniqueKeysWithValues: packQuestions.map { ($0.identity, $0.question) })
        let insights = StudyInsights.derive(
            envelope: progress.envelope,
            catalog: catalogMap,
            pending: progress.unsavedAnswers,
            now: Date()
        )
        let dueCount = insights.due.due

        return Button {
            onSelectCourse(pack.id)
            dismiss()
        } label: {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text(pack.subject)
                        .font(.headline.weight(.semibold))
                        .foregroundStyle(QuizzlerTheme.textPrimary)
                    Spacer()
                    badge(isSelected: isSelected, dueCount: dueCount)
                }

                let fraction = total > 0 ? Double(min(seen, total)) / Double(total) : 0.0
                ProgressView(value: fraction)
                    .progressViewStyle(.linear)
                    .tint(QuizzlerTheme.primaryCyan)

                Text(scheduledReviewEnabled ? "\(seen) of \(total) seen · \(dueCount) due" : "\(seen) of \(total) seen")
                    .font(.footnote)
                    .foregroundStyle(QuizzlerTheme.textMuted)
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
            .overlay(
                RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius)
                    .stroke(isSelected ? QuizzlerTheme.primaryCyan : QuizzlerTheme.border, lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("course-card-\(pack.id)")
        .accessibilityLabel(pack.subject)
        .accessibilityValue(
            scheduledReviewEnabled
                ? "\(seen) of \(total) seen, \(dueCount > 0 ? "\(dueCount) due" : "nothing due")"
                : "\(seen) of \(total) seen"
        )
        .accessibilityAddTraits(isSelected ? [.isSelected] : [])
    }

    @ViewBuilder
    private func badge(isSelected: Bool, dueCount: Int) -> some View {
        if isSelected {
            Text("Studying")
                .font(.caption.weight(.medium))
                .foregroundStyle(QuizzlerTheme.primaryCyan)
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(QuizzlerTheme.primaryCyan.opacity(0.15), in: Capsule())
        } else if scheduledReviewEnabled {
            let label = dueCount > 0 ? "\(dueCount) due" : "Nothing due"
            Text(label)
                .font(.caption.weight(.medium))
                .foregroundStyle(QuizzlerTheme.textMuted)
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(QuizzlerTheme.raisedCard, in: Capsule())
        }
    }
}

#if targetEnvironment(macCatalyst)
@MainActor
private final class IssueInboxModel: ObservableObject {
    @Published private(set) var receivedCount: Int = 0
    @Published private(set) var lastSuccessfulCheckTime: Date?
    @Published private(set) var isCheckRunning: Bool = false
    @Published private(set) var lastFailureReason: String?

    private let reader: IssueInboxReader?

    init() {
        let destinationURL = Self.inboxFileURL()

        if let destinationURL,
           FileManager.default.fileExists(atPath: destinationURL.path),
           let data = try? Data(contentsOf: destinationURL),
           let document = try? JSONDecoder().decode(IssueInboxDocument.self, from: data) {
            self.receivedCount = document.issues.count
        }

        if !Self.isUITestingOrXCTest, let destinationURL {
            let source = CloudKitIssueInboxSource(containerIdentifier: "iCloud.com.zerodelta.quizzler.dev")
            self.reader = IssueInboxReader(source: source, fileURL: destinationURL)
        } else {
            self.reader = nil
        }
    }

    func refresh() {
        guard !isCheckRunning else { return }
        guard !Self.isUITestingOrXCTest else { return }
        guard let reader else { return }

        isCheckRunning = true
        Task {
            do {
                let summary = try await reader.refresh()
                self.receivedCount = summary.totalCount
                self.lastSuccessfulCheckTime = Date()
                self.lastFailureReason = nil
            } catch let error as IssueInboxSourceError {
                switch error {
                case .changeTokenExpired:
                    self.lastFailureReason = "Change token expired · please check again"
                case .zoneNotFound:
                    self.lastFailureReason = "Question reports zone not found"
                case .unreadableIssueRecord:
                    self.lastFailureReason = "A question report could not be read. Update Quizzler on this Mac, then check again."
                }
            } catch is DecodingError, is IssueInboxDocumentError {
                self.lastFailureReason = "Local question reports store is unreadable"
            } catch {
                self.lastFailureReason = "Could not sync question reports from CloudKit"
            }
            self.isCheckRunning = false
        }
    }

    private static func inboxFileURL() -> URL? {
        guard let applicationSupport = try? FileManager.default.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        ) else { return nil }
        return applicationSupport
            .appendingPathComponent("Quizzler", isDirectory: true)
            .appendingPathComponent("issue-inbox-v1.json", isDirectory: false)
    }

    private static var isUITestingOrXCTest: Bool {
#if DEBUG
        if UITestFixture.isRunningUnderXCTest
            || UITestFixture.usesLocalProgress
            || UITestFixture.cloudStatusScript(environment: ProcessInfo.processInfo.environment) != nil {
            return true
        }
#endif
        let env = ProcessInfo.processInfo.environment
        return env["XCTestConfigurationFilePath"] != nil
            || env["QUIZZLER_UI_TEST_FIXTURE"] == "enabled"
            || env["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] == "enabled"
            || env["QUIZZLER_UI_TEST_CLOUD_STATUS"] != nil
    }
}
#endif

private struct SettingsView: View {
    @ObservedObject var catalog: StudyCatalogModel
    @ObservedObject var progress: LaunchpadProgressModel
    @AppStorage(StudySessionLength.key) private var storedSessionLength = StudySessionLength.default
    @AppStorage(StudyScheduledReview.key) private var scheduledReviewEnabled = StudyScheduledReview.default
    @State private var reviewExplanationPresented = false
#if targetEnvironment(macCatalyst)
    @ObservedObject var issueInbox: IssueInboxModel
#endif

    var body: some View {
        Form {
            Section("Study") {
                Picker("Default session limit", selection: $storedSessionLength) {
                    ForEach(StudySessionLength.options, id: \.self) { option in
                        Text(StudySessionLength.label(option))
                            .tag(option)
                    }
                }
                .accessibilityIdentifier("settings-default-session-limit")
                Text("Maximum questions for each new session. Today can choose a different limit once.")
                    .font(.caption)
                    .foregroundStyle(QuizzlerTheme.textMuted)

                Toggle("Offer scheduled reviews", isOn: $scheduledReviewEnabled)
                    .tint(QuizzlerTheme.primaryCyan)
                    .accessibilityIdentifier("settings-scheduled-review")
                Text("Spaced repetition of previously seen questions.")
                    .font(.caption)
                    .foregroundStyle(QuizzlerTheme.textMuted)

                Picker("Maximum Leitner level", selection: maximumLevelSelection) {
                    ForEach(1...7, id: \.self) { level in
                        Text("Level \(level) · \(LeitnerSchedule.intervalLabel(for: level))")
                            .tag(level)
                    }
                }
                .accessibilityIdentifier("settings-maximum-leitner-level")
                Text("Correct answers stop at this level. Lowering the maximum brings longer review dates forward.")
                    .font(.caption)
                    .foregroundStyle(QuizzlerTheme.textMuted)
                if let maximumLevelError = progress.maximumLevelError {
                    Text(maximumLevelError)
                        .font(.caption)
                        .foregroundStyle(QuizzlerTheme.danger)
                        .accessibilityIdentifier("settings-maximum-leitner-error")
                }

                Button("How scheduled reviews work") {
                    reviewExplanationPresented = true
                }
                .foregroundStyle(QuizzlerTheme.primaryCyan)
                .accessibilityIdentifier("settings-how-reviews-work")
            }
            if !catalog.failures.isEmpty {
                // A pack that was bundled but refused is reported here rather
                // than dropped, so the course going missing has a stated cause.
                Section("Packs not loaded") {
                    ForEach(catalog.failures, id: \.path) { failure in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(failure.path).font(.subheadline.weight(.semibold))
                            Text(failure.reason).font(.caption).foregroundStyle(QuizzlerTheme.textMuted)
                        }
                    }
                }
            }
#if targetEnvironment(macCatalyst)
            Section("Question reports") {
                LabeledContent("Received", value: "\(issueInbox.receivedCount)")
                    .accessibilityIdentifier("issue-inbox-received")
                LabeledContent("Last checked", value: lastCheckedDescription)
                    .accessibilityIdentifier("issue-inbox-last-checked")
                HStack(spacing: 8) {
                    Button("Check now") {
                        issueInbox.refresh()
                    }
                    .foregroundStyle(QuizzlerTheme.primaryCyan)
                    .disabled(issueInbox.isCheckRunning)
                    .accessibilityIdentifier("issue-inbox-check-now")

                    if issueInbox.isCheckRunning {
                        ProgressView()
                            .controlSize(.small)
                    }
                }
                if let failure = issueInbox.lastFailureReason {
                    Text(failure)
                        .font(.caption)
                        .foregroundStyle(QuizzlerTheme.danger)
                        .lineLimit(1)
                        .accessibilityIdentifier("issue-inbox-error")
                }
            }
#endif
            Section("About") {
                LabeledContent {
                    Text(NativeAppVersion.display)
                } label: {
                    Text("App version")
                }
                .accessibilityIdentifier("settings-app-version")
                Text("Question packs and your selected course stay on this device. Progress syncs through your iCloud account. Reports include question context only.")
            }
        }
        .scrollContentBackground(.hidden)
        .background(QuizzlerTheme.terminalBackground)
        .foregroundStyle(QuizzlerTheme.textPrimary)
        .sheet(isPresented: $reviewExplanationPresented) {
            ScheduledReviewsExplanationView(maximumLevel: progress.maximumLeitnerLevel)
        }
    }

    private var maximumLevelSelection: Binding<Int> {
        Binding(
            get: { progress.maximumLeitnerLevel },
            set: { progress.setMaximumLeitnerLevel($0) }
        )
    }

#if targetEnvironment(macCatalyst)
    private var lastCheckedDescription: String {
        guard let lastCheckTime = issueInbox.lastSuccessfulCheckTime else {
            return "Not yet"
        }
        let formatter = RelativeDateTimeFormatter()
        return formatter.localizedString(for: lastCheckTime, relativeTo: Date())
    }
#endif

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

func eyebrow(_ text: String) -> some View {
    Text(text.uppercased())
        .font(QuizzlerTheme.metadataFont)
        .foregroundStyle(QuizzlerTheme.primaryCyan)
}

// MARK: - Mac Catalyst Window and Layout Shaping

private struct TabContentWidthCapModifier: ViewModifier {
    func body(content: Content) -> some View {
        content
            .frame(maxWidth: 560)
            .frame(maxWidth: .infinity, alignment: .center)
            // The margins beside the column on a wide iPad or Mac window.
            .background(QuizzlerTheme.terminalBackground.ignoresSafeArea())
#if targetEnvironment(macCatalyst)
            // `CatalystTabBar` replaces the system bar on the Mac.
            .toolbar(.hidden, for: .tabBar)
#endif
    }
}

private extension View {
    func cappedTabContentWidth() -> some View {
        modifier(TabContentWidthCapModifier())
    }

    func todayActionSurface() -> some View {
        padding(.horizontal, 16)
            .frame(maxWidth: .infinity, minHeight: QuizzlerTheme.minimumTouchTarget)
            .background(QuizzlerTheme.raisedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
            .overlay(
                RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius)
                    .stroke(QuizzlerTheme.border, lineWidth: 1)
            )
    }
}

#if targetEnvironment(macCatalyst)
/// The phone's floating bottom tab bar, drawn for the Mac. Catalyst hosts
/// `TabView`'s own tabs in the window toolbar, where a phone-width window
/// collapses them to a titlebar popup, so the Mac hides that bar.
private struct CatalystTabBar: View {
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
            }
        }
        .padding(4)
        .background(QuizzlerTheme.elevatedCard, in: Capsule())
        .overlay(Capsule().stroke(QuizzlerTheme.border, lineWidth: 1))
        .frame(maxWidth: .infinity)
        .padding(.bottom, 12)
    }
}

private struct CatalystWindowShaper: UIViewRepresentable {
    func makeUIView(context: Context) -> ShaperView {
        ShaperView()
    }

    func updateUIView(_ uiView: ShaperView, context: Context) {}

    final class ShaperView: UIView {
        private var configured = false

        override func didMoveToWindow() {
            super.didMoveToWindow()
            guard !configured, let windowScene = window?.windowScene else { return }
            configured = true
            windowScene.sizeRestrictions?.minimumSize = CGSize(width: 380, height: 600)
            windowScene.sizeRestrictions?.maximumSize = CGSize(width: 560, height: CGFloat.greatestFiniteMagnitude)
            windowScene.traitOverrides.horizontalSizeClass = .compact
        }
    }
}
#endif
