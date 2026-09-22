import Foundation
import SwiftUI
import QuizzlerKit

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

/// The questions this session will serve, and how far through them we are.
/// `nil` between sessions, when the position follows from saved progress instead.
/// It is pinned for the duration of a session so recording an answer cannot
/// swap the question out from under the Feedback screen.
struct ActiveSession {
    let mode: SelectionMode
    let questions: [StudyQuestion]
    var position: Int
    var answers: [SessionAnswer]
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

    // Hardcoded batch size for normal sessions. A setting can replace this later.
    private let sessionLength = 10

    init(repository: any LaunchpadProgressRepository, catalog: StudyCatalogModel = StudyCatalogModel()) {
        self.repository = repository
        _progress = StateObject(wrappedValue: LaunchpadProgressModel(repository: repository))
        _catalog = StateObject(wrappedValue: catalog)
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
                    syncChip
                }
                .background(QuizzlerTheme.terminalBackground.ignoresSafeArea())
                .toolbar(.hidden, for: .navigationBar)
            }
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
            .tabItem {
                Label(LaunchpadState.progress.title, systemImage: LaunchpadState.progress.icon)
            }
            .tag(LaunchpadState.progress)

            NavigationStack {
                SettingsView(
                    catalog: catalog,
                    persistenceState: progress.persistenceState,
                    onSelectCourse: selectCourse,
                    onRetrySync: progress.saveCurrentSession
                )
                .navigationTitle("Settings")
            }
            .tabItem {
                Label(LaunchpadState.settings.title, systemImage: LaunchpadState.settings.icon)
            }
            .tag(LaunchpadState.settings)
        }
        .preferredColorScheme(.dark)
        .tint(QuizzlerTheme.primaryCyan)
        .task {
            progress.load()
            catalog.loadPacks()
        }
        .onChange(of: scenePhase) { _, newPhase in
            guard newPhase == .active else { return }
            progress.synchronizeOnForeground()
        }
    }

    private var syncChip: some View {
        HStack(spacing: 8) {
            Text(persistenceStatus)
                .font(.caption)
                .foregroundStyle(QuizzlerTheme.textMuted)
                .lineLimit(1)
            if progress.persistenceState == .saveFailed {
                Button("Retry save", action: progress.saveCurrentSession)
                    .buttonStyle(.bordered)
                    .tint(QuizzlerTheme.primaryCyan)
                    .accessibilityHint("Retries saving the recorded answer")
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .background(QuizzlerTheme.elevatedCard, in: Capsule())
        .padding(.bottom, 8)
    }

    private var persistenceStatus: String {
        switch progress.persistenceState {
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
        switch state {
        case .today:
            TodayView(
                courseTitle: pack.subject,
                questionNumber: resumeIndex(count: questions.count) + 1,
                questionCount: questions.count,
                correct: activeAggregate.correct,
                answered: activeAggregate.answered,
                dueCount: currentInsights.due.due,
                missedCount: currentInsights.recentMisses.count,
                onStart: startSession,
                onStartDueReview: startDueReview,
                onStartRetryMissed: startRetryMissed,
                onProgress: { state = .progress }
            )
        case .question:
            QuestionShellView(
                studyQuestion: question,
                phase: .question,
                sessionPosition: sessionPosition,
                repository: repository,
                selection: $selection,
                onCheck: checkAnswer,
                onFinish: {}
            )
        case .feedback:
            QuestionShellView(
                studyQuestion: question,
                phase: .feedback(correct: isCorrect(question)),
                sessionPosition: sessionPosition,
                repository: repository,
                selection: $selection,
                onCheck: { _ in },
                onFinish: finishQuestion
            )
        case .results:
            if let session = activeSession {
                SessionSummaryView(
                    session: session,
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
                    correct: activeAggregate.correct,
                    answered: activeAggregate.answered,
                    dueCount: currentInsights.due.due,
                    missedCount: currentInsights.recentMisses.count,
                    onStart: startSession,
                    onStartDueReview: startDueReview,
                    onStartRetryMissed: startRetryMissed,
                    onProgress: { state = .progress }
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
        guard let request = try? SelectionRequest(mode: .normal, limit: sessionLength) else { return }
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
        activeSession = ActiveSession(mode: .normal, questions: sessionQuestions, position: 0, answers: [])
        state = .question
    }

    private func startDueReview() {
        startModeSession(mode: .srs, count: currentInsights.due.due)
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
        activeSession = ActiveSession(mode: .retryMissed, questions: sessionQuestions, position: 0, answers: [])
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
        activeSession = ActiveSession(mode: mode, questions: sessionQuestions, position: 0, answers: [])
        state = .question
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
        guard let question = currentQuestion, var session = activeSession else { return }
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
        guard var session = activeSession else { return }
        let allQuestions = catalog.questions
        let questionCount = allQuestions.count
        guard questionCount > 0 else { return }
        progress.saveCurrentSession()
        selection = .none

        // For .normal sessions, advance the pack-level resume position after each
        // answer so a relaunch resumes at exactly the next unreviewed question.
        // Other modes must not write the position — they serve a curated subset.
        if session.mode == .normal, let pack = catalog.pack {
            // The next pack index is computed from the current session question,
            // not the session position, because the plan may not start at index 0.
            let currentPackIndex = allQuestions.firstIndex(where: { $0.identity == session.questions[session.position].identity }) ?? 0
            let nextPackIndex = (currentPackIndex + 1) % questionCount
            StudyResumePosition.store(
                nextPackIndex,
                courseID: pack.courseID,
                packID: pack.packID,
                questionCount: questionCount
            )
        }

        let nextPosition = session.position + 1
        if nextPosition >= session.questions.count {
            // Session exhausted — show the summary with the completed session snapshot.
            activeSession = session
            state = .results
        } else {
            session.position = nextPosition
            activeSession = session
            state = .question
        }
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
private struct TodayView: View {
    let courseTitle: String
    let questionNumber: Int
    let questionCount: Int
    let correct: Int
    let answered: Int
    let dueCount: Int
    let missedCount: Int
    let onStart: () -> Void
    let onStartDueReview: () -> Void
    let onStartRetryMissed: () -> Void
    let onProgress: () -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                eyebrow("Today · \(courseTitle)")
                Text("A focused review, ready when you are.")
                    .font(.largeTitle.weight(.bold))
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                VStack(alignment: .leading, spacing: 14) {
                    HStack {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Continue review")
                                .font(.headline)
                            Text("Question \(questionNumber) of \(questionCount)")
                                .font(.subheadline)
                                .foregroundStyle(QuizzlerTheme.textMuted)
                                .accessibilityIdentifier("today-position")
                        }
                        Spacer()
                        Text("\(correct)/\(answered)")
                            .font(.title2.monospacedDigit().weight(.semibold))
                            .foregroundStyle(QuizzlerTheme.primaryCyan)
                            .accessibilityLabel("\(correct) correct of \(answered) answered")
                            .accessibilityIdentifier("today-score")
                    }
                    Button("Start review", action: onStart)
                        .buttonStyle(.borderedProminent)
                        .tint(QuizzlerTheme.primaryCyan)
                        .foregroundStyle(.black)
                        .frame(maxWidth: .infinity, minHeight: 48)
                }
                .padding(18)
                .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))

                dueReviewRow

                retryMissedRow

                Button("View progress", action: onProgress)
                    .buttonStyle(.bordered)
                    .tint(QuizzlerTheme.primaryCyan)
                    .frame(maxWidth: .infinity, minHeight: 44)
            }
            .padding(QuizzlerTheme.pageGutter)
            .padding(.bottom, QuizzlerTheme.scrollBottomInset)
        }
        .background(QuizzlerTheme.terminalBackground)
    }

    private var dueReviewRow: some View {
        Button(action: onStartDueReview) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Due for review")
                        .font(.headline)
                        .foregroundStyle(dueCount > 0 ? QuizzlerTheme.textPrimary : QuizzlerTheme.textMuted)
                    if dueCount == 0 {
                        Text("No questions due for review right now.")
                            .font(.subheadline)
                            .foregroundStyle(QuizzlerTheme.textMuted)
                    } else {
                        Text("\(dueCount) question\(dueCount == 1 ? "" : "s") ready to review")
                            .font(.subheadline)
                            .foregroundStyle(QuizzlerTheme.textMuted)
                    }
                }
                Spacer()
                Text("\(dueCount)")
                    .font(.title2.monospacedDigit().weight(.semibold))
                    .foregroundStyle(dueCount > 0 ? QuizzlerTheme.primaryCyan : QuizzlerTheme.textMuted)
            }
            .padding(18)
            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
            .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
        }
        .buttonStyle(.plain)
        .disabled(dueCount == 0)
        .accessibilityIdentifier("today-due-review")
    }

    private var retryMissedRow: some View {
        Button(action: onStartRetryMissed) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Retry missed")
                        .font(.headline)
                        .foregroundStyle(missedCount > 0 ? QuizzlerTheme.textPrimary : QuizzlerTheme.textMuted)
                    if missedCount == 0 {
                        Text("No recently missed questions.")
                            .font(.subheadline)
                            .foregroundStyle(QuizzlerTheme.textMuted)
                    } else {
                        Text("\(missedCount) question\(missedCount == 1 ? "" : "s") to retry")
                            .font(.subheadline)
                            .foregroundStyle(QuizzlerTheme.textMuted)
                    }
                }
                Spacer()
                Text("\(missedCount)")
                    .font(.title2.monospacedDigit().weight(.semibold))
                    .foregroundStyle(missedCount > 0 ? QuizzlerTheme.primaryCyan : QuizzlerTheme.textMuted)
            }
            .padding(18)
            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
            .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
        }
        .buttonStyle(.plain)
        .disabled(missedCount == 0)
        .accessibilityIdentifier("today-retry-missed")
    }
}

private struct SettingsView: View {
    @ObservedObject var catalog: StudyCatalogModel
    let persistenceState: LaunchpadProgressModel.PersistenceState
    let onSelectCourse: @MainActor (String) -> Void
    let onRetrySync: () -> Void

    var body: some View {
        Form {
            Section("Study") {
                if catalog.availablePacks.isEmpty {
                    LabeledContent("Course", value: catalog.courseTitle)
                } else {
                    Picker("Course", selection: Binding(
                        get: { catalog.selectedPackKey ?? "" },
                        set: { packKey in onSelectCourse(packKey) }
                    )) {
                        ForEach(catalog.availablePacks) { pack in
                            Text("\(pack.subject) · \(pack.questions.count) questions")
                                .tag(pack.id)
                        }
                    }
                    .accessibilityIdentifier("course-selector")
                }
                LabeledContent("App version", value: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1.0.0")
                LabeledContent("Progress", value: progressLabel)
                if persistenceState == .syncPending {
                    Button("Retry sync", action: onRetrySync)
                        .tint(QuizzlerTheme.primaryCyan)
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
            Section("About") {
                Text("Question packs and your selected course stay on this device. Progress syncs through your iCloud account. Reports include question context only.")
            }
        }
        .scrollContentBackground(.hidden)
        .background(QuizzlerTheme.terminalBackground)
        .foregroundStyle(QuizzlerTheme.textPrimary)
    }

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

func eyebrow(_ text: String) -> some View {
    Text(text.uppercased())
        .font(QuizzlerTheme.metadataFont)
        .foregroundStyle(QuizzlerTheme.primaryCyan)
}
