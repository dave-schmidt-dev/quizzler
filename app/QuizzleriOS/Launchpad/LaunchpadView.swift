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

    private let repository: any LaunchpadProgressRepository
    private let beforeSave: @Sendable () async -> Void
    /// Published so study surfaces can derive insights from the same
    /// envelope the counters come from, rather than keeping a second copy
    /// that can drift from it.
    @Published private(set) var envelope: ProgressEnvelope?
    private var saveIsInFlight = false
    private var syncIsInFlight = false
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

    /// Returns counters for one pack. The envelope's mastery facts survive
    /// operation compaction, unlike its bounded session-detail history.
    func aggregate(for pack: InstalledPack?) -> AggregateSnapshot {
        guard let pack else {
            return AggregateSnapshot(answered: unsavedAnswers.count, correct: unsavedAnswers.filter(\.correct).count)
        }
        return aggregate(courseID: pack.courseID, packID: pack.packID)
    }

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
        case .synced: "progress synced"
        case .syncPending: "progress saved here · sync pending"
        case .accountChanged: "iCloud account changed · local history kept safe"
        case .saveFailed: "local save failed · retry required"
        }
    }

    func load() {
        persistenceState = .loading
        startProgressObservation()
        startSyncStatusObservation()
        Task {
            do {
                apply(try await repository.snapshot())
                guard repository.syncMode == .cloudKit else {
                    persistenceState = .local
                    return
                }
                startSynchronization()
            } catch {
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
                apply(try await repository.snapshot())
                syncIsInFlight = false
                if unsavedAnswers.isEmpty {
                    persistenceState = .synced
                } else {
                    persistNextBatch()
                }
            } catch {
                syncIsInFlight = false
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

    /// The number of questions to request. A stored value that is not one of
    /// the offered options falls back to the default rather than trusting it,
    /// so a corrupted or hand-edited default cannot request a nonsense limit.
    static func limit(stored: Int, packQuestionCount: Int) -> Int {
        guard packQuestionCount > 0 else { return 0 }
        guard options.contains(stored) else { return min(`default`, packQuestionCount) }
        if stored == wholePack { return packQuestionCount }
        return min(stored, packQuestionCount)
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

    init(due: Int, unseen: Int, sessionLimit: Int) {
        if due > 0 {
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
        case .review(_, let due):
            "\(due) \(due == 1 ? "question due" : "questions due")"
        case .learn:
            "Nothing due"
        case .caughtUp:
            "All caught up"
        }
    }

    var detail: String {
        let minuteWord = minutes == 1 ? "minute" : "minutes"
        switch self {
        case .review:
            return "About \(minutes) \(minuteWord)"
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

/// Formats the date line as weekday + "morning" (<12h) / "afternoon" (<17h) / "evening".
enum TodayDateLineFormatter {
    static func format(date: Date = Date(), calendar: Calendar = .current) -> String {
        let hour = calendar.component(.hour, from: date)
        let period: String
        if hour < 12 {
            period = "morning"
        } else if hour < 17 {
            period = "afternoon"
        } else {
            period = "evening"
        }
        let formatter = DateFormatter()
        formatter.calendar = calendar
        formatter.timeZone = calendar.timeZone
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "EEEE"
        let weekday = formatter.string(from: date)
        return "\(weekday) \(period)"
    }
}

func todayDateLine(date: Date = Date(), calendar: Calendar = .current) -> String {
    TodayDateLineFormatter.format(date: date, calendar: calendar)
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
    private let repository: any LaunchpadProgressRepository
    @StateObject private var progress: LaunchpadProgressModel
    @StateObject private var catalog: StudyCatalogModel
#if targetEnvironment(macCatalyst)
    @StateObject private var issueInbox = IssueInboxModel()
#endif

    /// Chosen on Today and saved for the next session.
    @AppStorage(StudySessionLength.key) private var storedSessionLength = StudySessionLength.default

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

    private var activeAggregate: AggregateSnapshot {
        progress.aggregate(for: catalog.pack)
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
                .overlay(alignment: .top) { StatusBarScrim() }
                .navigationTitle("Today")
                .toolbar(.hidden, for: .navigationBar)
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
                    persistenceState: progress.persistenceState,
                    onRetrySync: progress.saveCurrentSession
                )
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
                    persistenceState: progress.persistenceState,
                    onRetrySync: progress.saveCurrentSession,
                    issueInbox: issueInbox
                )
                .navigationTitle("Settings")
#else
                SettingsView(
                    catalog: catalog,
                    persistenceState: progress.persistenceState,
                    onRetrySync: progress.saveCurrentSession
                )
                .navigationTitle("Settings")
#endif
            }
            .cappedTabContentWidth()
            .tabItem {
                Label(LaunchpadState.settings.title, systemImage: LaunchpadState.settings.icon)
            }
            .tag(LaunchpadState.settings)
        }
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

    @ViewBuilder private var studyContent: some View {
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

    @ViewBuilder private func readyContent(pack: InstalledPack, questions: [StudyQuestion], question: StudyQuestion) -> some View {
        let seenCount = progress.seenIdentities(courseID: pack.courseID, packID: pack.packID).count
        let unseenCount = max(0, questions.count - seenCount)
        switch state {
        case .today:
            TodayView(
                courseTitle: pack.subject,
                questionNumber: resumeIndex(count: questions.count) + 1,
                questionCount: questions.count,
                unseenCount: unseenCount,
                correct: activeAggregate.correct,
                answered: activeAggregate.answered,
                dueCount: currentInsights.due.due,
                missedCount: currentInsights.recentMisses.count,
                persistenceState: progress.persistenceState,
                persistenceStatus: progress.persistenceStatus,
                catalog: catalog,
                progress: progress,
                onStart: startSession,
                onStartDueReview: startDueReview,
                onStartRetryMissed: startRetryMissed,
                onSelectCourse: selectCourse,
                onRetrySave: progress.saveCurrentSession
            )
        case .question:
            QuestionShellView(
                studyQuestion: question,
                phase: .question,
                sessionPosition: sessionPosition,
                repository: repository,
                selection: $selection,
                onCheck: checkAnswer,
                onFinish: {},
                onSkip: skipQuestion,
                onEnd: endSession
            )
        case .feedback:
            QuestionShellView(
                studyQuestion: question,
                phase: .feedback(correct: isCorrect(question)),
                sessionPosition: sessionPosition,
                repository: repository,
                selection: $selection,
                onCheck: { _ in },
                onFinish: finishQuestion,
                onSkip: skipQuestion,
                onEnd: endSession
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
                    courseTitle: pack.subject,
                    questionNumber: resumeIndex(count: questions.count) + 1,
                    questionCount: questions.count,
                    unseenCount: unseenCount,
                    correct: activeAggregate.correct,
                    answered: activeAggregate.answered,
                    dueCount: currentInsights.due.due,
                    missedCount: currentInsights.recentMisses.count,
                    persistenceState: progress.persistenceState,
                    persistenceStatus: progress.persistenceStatus,
                    catalog: catalog,
                    progress: progress,
                    onStart: startSession,
                    onStartDueReview: startDueReview,
                    onStartRetryMissed: startRetryMissed,
                    onSelectCourse: selectCourse,
                    onRetrySave: progress.saveCurrentSession
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
        let limit = StudySessionLength.limit(stored: storedSessionLength, packQuestionCount: questions.count)
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
        state = .question
    }

    private func startDueReview() {
        let questions = catalog.questions
        let limit = StudySessionLength.limit(stored: storedSessionLength, packQuestionCount: questions.count)
        startModeSession(mode: .srs, count: min(currentInsights.due.due, limit))
    }

    private func startRetryMissed() {
        startModeSession(mode: .retryMissed, count: currentInsights.recentMisses.count)
    }

    /// Starts a new retryMissed session seeded from the just-finished session's
    /// wrong answers, so the learner re-drills exactly what they missed without
    /// mixing in new SRS-due questions.
    private func startRetryMissedFromSession() {
        guard let session = activeSession else { return }
        let wrongIdentities = session.answers.filter { !$0.correct }.map(\.identity)
        guard !wrongIdentities.isEmpty else { return }
        let questions = catalog.questions
        let sessionQuestions = wrongIdentities.compactMap { identity in
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
        selection = .none
        state = .today
    }

    private func checkAnswer(_: Bool) {
        // Answering on tap can deliver a second selection change before the
        // Feedback screen replaces the question; only the first one counts.
        guard state == .question, let question = currentQuestion, var session = activeSession else { return }
        let correct = isCorrect(question)
        let answer = SessionAnswer(identity: question.identity, correct: correct)
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
/// pack or the progress repository. An earlier build printed a fixed position
/// and a fixed score as literal text over a three-question array (walkthrough
/// finding 2), which is why these are parameters and why
/// `TodayCounterSourceTests` asserts those literals never return.
/// The first screen a tester sees. Every number on it comes from the installed
/// pack or the progress repository.
struct TodayView: View {
    let courseTitle: String
    let questionNumber: Int
    let questionCount: Int
    let unseenCount: Int
    let correct: Int
    let answered: Int
    let dueCount: Int
    let missedCount: Int
    let persistenceState: LaunchpadProgressModel.PersistenceState
    let persistenceStatus: String
    let catalog: StudyCatalogModel
    let progress: LaunchpadProgressModel
    let onStart: () -> Void
    let onStartDueReview: () -> Void
    let onStartRetryMissed: () -> Void
    let onSelectCourse: (String) -> Void
    let onRetrySave: () -> Void

    @AppStorage(StudySessionLength.key) private var storedSessionLength = StudySessionLength.default

    private var recommendation: TodayRecommendation {
        let limit = StudySessionLength.limit(stored: storedSessionLength, packQuestionCount: questionCount)
        return TodayRecommendation(due: dueCount, unseen: unseenCount, sessionLimit: limit)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                dateAndTitle

                heroCard

                quietListCard

                statusLine
            }
            .padding(QuizzlerTheme.pageGutter)
            .padding(.bottom, QuizzlerTheme.scrollBottomInset)
        }
        .background(QuizzlerTheme.terminalBackground)
        .navigationTitle("Today")
        .toolbar(.hidden, for: .navigationBar)
    }

    private var dateAndTitle: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(TodayDateLineFormatter.format())
                .font(.subheadline)
                .foregroundStyle(QuizzlerTheme.textMuted)
            Text("Ready when you are")
                .font(.largeTitle.weight(.bold))
                .foregroundStyle(QuizzlerTheme.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var heroCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text(courseTitle)
                    .font(.headline)
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                Spacer()
                NavigationLink {
                    CoursesView(
                        catalog: catalog,
                        progress: progress,
                        onSelectCourse: onSelectCourse
                    )
                } label: {
                    HStack(spacing: 4) {
                        Text("Change")
                        Image(systemName: "chevron.right")
                            .font(.caption.weight(.semibold))
                    }
                    .font(.subheadline)
                    .foregroundStyle(QuizzlerTheme.primaryCyan)
                    .frame(minHeight: QuizzlerTheme.minimumTouchTarget)
                    .contentShape(Rectangle())
                }
                .accessibilityLabel("Change course")
                .accessibilityIdentifier("today-change-course")
            }

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
        }
        .padding(18)
        .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
        .overlay(
            RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius)
                .stroke(QuizzlerTheme.primaryCyan.opacity(0.3), lineWidth: 1)
        )
    }

    private var quietListCard: some View {
        VStack(spacing: 0) {
            learnNewRow
            Divider().background(QuizzlerTheme.border)
            retryMissedRow
            Divider().background(QuizzlerTheme.border)
            sessionLengthRow
        }
        .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
        .overlay(
            RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius)
                .stroke(QuizzlerTheme.border, lineWidth: 1)
        )
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
                Image(systemName: "chevron.right")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(QuizzlerTheme.textMuted)
            }
            .padding(.horizontal, 16)
            .frame(maxWidth: .infinity, minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Learn new questions")
        .accessibilityIdentifier("today-learn-new")
        .accessibilityValue("Question \(questionNumber) of \(questionCount)")
    }

    private var retryMissedRow: some View {
        Button(action: onStartRetryMissed) {
            HStack {
                Text("Retry missed")
                    .font(.body)
                    .foregroundStyle(missedCount > 0 ? QuizzlerTheme.textPrimary : QuizzlerTheme.textMuted)
                Spacer()
                Text("\(missedCount)")
                    .font(.body.monospacedDigit())
                    .foregroundStyle(QuizzlerTheme.textMuted)
                Image(systemName: "chevron.right")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(QuizzlerTheme.textMuted)
            }
            .padding(.horizontal, 16)
            .frame(maxWidth: .infinity, minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(missedCount == 0)
        .accessibilityLabel("Retry missed")
        .accessibilityIdentifier("today-retry-missed")
    }

    private var sessionLengthRow: some View {
        Menu {
            ForEach(StudySessionLength.options, id: \.self) { option in
                Button {
                    storedSessionLength = option
                } label: {
                    HStack {
                        Text(StudySessionLength.label(option))
                        if storedSessionLength == option {
                            Image(systemName: "checkmark")
                        }
                    }
                }
            }
        } label: {
            HStack {
                Text("Session length")
                    .font(.body)
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                Spacer()
                Text(StudySessionLength.label(storedSessionLength))
                    .font(.body)
                    .foregroundStyle(QuizzlerTheme.textMuted)
                Image(systemName: "chevron.right")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(QuizzlerTheme.textMuted)
            }
            .padding(.horizontal, 16)
            .frame(maxWidth: .infinity, minHeight: 44)
            .contentShape(Rectangle())
        }
        .accessibilityLabel("Session length")
        .accessibilityValue(StudySessionLength.label(storedSessionLength))
        .accessibilityIdentifier("today-session-length")
    }

    private var scoreHalf: some View {
        Text("\(correct) of \(answered) right so far")
            .font(.footnote)
            .foregroundStyle(QuizzlerTheme.textMuted)
            .accessibilityIdentifier("today-score")
    }

    private var statusHalf: some View {
        HStack(spacing: 6) {
            Image(systemName: persistenceState == .synced ? "checkmark.icloud" : "icloud")
                .font(.footnote)
                .foregroundStyle(QuizzlerTheme.textMuted)
            Text(persistenceStatus)
                .font(.footnote)
                .foregroundStyle(QuizzlerTheme.textMuted)
                .fixedSize(horizontal: false, vertical: true)
            if persistenceState == .saveFailed {
                Button("Retry save", action: onRetrySave)
                    .buttonStyle(.bordered)
                    .tint(QuizzlerTheme.primaryCyan)
                    .controlSize(.small)
                    .frame(minWidth: QuizzlerTheme.minimumTouchTarget, minHeight: QuizzlerTheme.minimumTouchTarget)
                    .accessibilityHint("Retries saving the recorded answer")
            }
        }
    }

    private var statusLine: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .center) {
                scoreHalf
                Spacer()
                statusHalf
            }
            VStack(alignment: .leading, spacing: 6) {
                scoreHalf
                statusHalf
            }
        }
        .padding(.top, 4)
    }
}

/// Lists all installed course packs and lets the learner switch courses.
struct CoursesView: View {
    @ObservedObject var catalog: StudyCatalogModel
    @ObservedObject var progress: LaunchpadProgressModel
    let onSelectCourse: (String) -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                ForEach(catalog.availablePacks) { pack in
                    courseCard(for: pack)
                }
            }
            .padding(QuizzlerTheme.pageGutter)
            .padding(.bottom, QuizzlerTheme.scrollBottomInset)
        }
        .background(QuizzlerTheme.terminalBackground.ignoresSafeArea())
        .navigationTitle("Your courses")
        .toolbar(.visible, for: .navigationBar)
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

                Text("\(seen) of \(total) seen · \(dueCount) due")
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
        .accessibilityValue("\(seen) of \(total) seen, \(dueCount > 0 ? "\(dueCount) due" : "nothing due")")
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
        } else {
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
    let persistenceState: LaunchpadProgressModel.PersistenceState
    let onRetrySync: () -> Void
#if targetEnvironment(macCatalyst)
    @ObservedObject var issueInbox: IssueInboxModel
#endif

    var body: some View {
        Form {
            Section("Sync") {
                LabeledContent("Progress", value: progressLabel)
                    .accessibilityIdentifier("settings-progress-status")
                if persistenceState == .syncPending {
                    // It was the only acting row in this group and looked like
                    // every inert label beside it. A filled label and an icon
                    // say it does something.
                    Button(action: onRetrySync) {
                        Label("Retry sync", systemImage: "arrow.clockwise")
                            .font(.body.weight(.semibold))
                            .foregroundStyle(QuizzlerTheme.primaryCyan)
                            .frame(minHeight: QuizzlerTheme.minimumTouchTarget)
                    }
                    .accessibilityLabel("Retry iCloud sync")
                    .accessibilityIdentifier("settings-retry-sync")
                }
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
                LabeledContent("App version", value: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1.0.0")
                Text("Question packs and your selected course stay on this device. Progress syncs through your iCloud account. Reports include question context only.")
            }
        }
        .scrollContentBackground(.hidden)
        .background(QuizzlerTheme.terminalBackground)
        .foregroundStyle(QuizzlerTheme.textPrimary)
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

    private var progressLabel: String {
        switch persistenceState {
        case .synced: "Synced with iCloud"
        case .syncing: "Syncing with iCloud"
        case .syncPending: "Saved here · sync pending"
        case .accountChanged: "iCloud account changed · local history kept safe"
        case .loading: "Loading progress"
        case .local, .saving: "Saved on this device"
        case .saveFailed: "Save needs attention"
        }
    }
}

/// An opaque strip over the status bar.
///
/// Scrolling content used to ride up behind the clock and the Dynamic Island
/// and stay legible there, colliding with them. The Today tab hides its
/// navigation bar, so there is no system scroll-edge treatment to inherit.
///
/// The height comes from the key window rather than from a `GeometryReader`:
/// two layout-derived attempts both measured zero here and rendered nothing,
/// and a strip of the wrong height is indistinguishable from no strip at all.
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
