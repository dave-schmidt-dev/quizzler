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
    private var envelope: ProgressEnvelope?
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

struct LaunchpadView: View {
    @Environment(\.scenePhase) private var scenePhase
    @State private var state: LaunchpadState = .today
    /// The question the current session is showing. `nil` between sessions,
    /// when the position follows from saved progress instead. It is pinned for
    /// the duration of a session so recording an answer cannot swap the
    /// question out from under the Feedback screen.
    @State private var sessionIndex: Int?
    @State private var selection: QuestionSelection = .none
    private let repository: any LaunchpadProgressRepository
    @StateObject private var progress: LaunchpadProgressModel
    @StateObject private var catalog: StudyCatalogModel

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
        return questions[sessionIndex ?? resumeIndex(count: questions.count)]
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

    var body: some View {
        VStack(spacing: 0) {
            consoleHeader
            content
            navigationBar
        }
        .preferredColorScheme(.dark)
        .background(QuizzlerTheme.terminalBackground.ignoresSafeArea())
        .task {
            progress.load()
            catalog.loadPacks()
        }
        .onChange(of: scenePhase) { _, newPhase in
            guard newPhase == .active else { return }
            progress.synchronizeOnForeground()
        }
    }

    private var consoleHeader: some View {
        HStack(alignment: .center, spacing: 10) {
            Text("Quizzler")
                .font(.title2.weight(.medium))
                .foregroundStyle(QuizzlerTheme.textPrimary)
            Text(syncStatus)
                .font(QuizzlerTheme.metadataFont)
                .foregroundStyle(QuizzlerTheme.textMuted)
                .lineLimit(1)
            Spacer(minLength: 0)
            if progress.persistenceState == .saveFailed {
                Button("Retry save", action: progress.saveCurrentSession)
                    .buttonStyle(.bordered)
                    .tint(QuizzlerTheme.primaryCyan)
                    .accessibilityHint("Retries saving the recorded answer")
            }
            Button {
                state = state == .settings ? .today : .settings
            } label: {
                Image(systemName: state == .settings ? "xmark" : "gearshape")
                    .font(.body.weight(.semibold))
                    .foregroundStyle(QuizzlerTheme.primaryCyan)
                    .frame(width: QuizzlerTheme.minimumTouchTarget, height: QuizzlerTheme.minimumTouchTarget)
            }
            .accessibilityLabel(state == .settings ? "Close settings" : "Open settings")
        }
        .padding(.horizontal, QuizzlerTheme.pageGutter)
        .padding(.top, 8)
        .padding(.bottom, 4)
        .background(QuizzlerTheme.terminalBackground)
    }

    private var syncStatus: String {
        switch state {
        case .today, .question, .progress:
            persistenceStatus
        case .feedback:
            "answer checked · \(persistenceStatus)"
        case .results:
            persistenceStatus
        case .settings: "settings"
        }
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

    @ViewBuilder private var content: some View {
        switch state {
        case .progress:
            // Progress and Settings describe the install itself, so they stay
            // reachable when no pack is available to study.
            ProgressView(
                answered: activeAggregate.answered,
                correct: activeAggregate.correct,
                persistenceState: progress.persistenceState,
                onRetrySync: progress.saveCurrentSession
            )
        case .settings:
            SettingsView(
                catalog: catalog,
                persistenceState: progress.persistenceState,
                onSelectCourse: selectCourse,
                onRetrySync: progress.saveCurrentSession
            )
        default:
            studyContent
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
                onStart: startSession,
                onProgress: { state = .progress }
            )
        case .question:
            QuestionShellView(
                studyQuestion: question,
                phase: .question,
                repository: repository,
                selection: $selection,
                onCheck: checkAnswer,
                onFinish: {}
            )
        case .feedback:
            QuestionShellView(
                studyQuestion: question,
                phase: .feedback(correct: isCorrect(question)),
                repository: repository,
                selection: $selection,
                onCheck: { _ in },
                onFinish: finishQuestion
            )
        case .results:
            ResultsView(
                answered: activeAggregate.answered,
                correct: activeAggregate.correct,
                saving: progress.persistenceState == .saving,
                saveFailed: progress.persistenceState == .saveFailed,
                syncPending: progress.persistenceState == .syncPending,
                accountChanged: progress.persistenceState == .accountChanged,
                onRetrySave: progress.saveCurrentSession,
                onNext: startSession,
                onProgress: { state = .progress }
            )
        case .progress, .settings:
            EmptyView()
        }
    }

    private var navigationBar: some View {
        HStack(spacing: 0) {
            ForEach(LaunchpadState.primaryNavigationStates) { destination in
                Button {
                    state = destination
                } label: {
                    Image(systemName: destination.icon)
                        .font(.body.weight(.semibold))
                        .foregroundStyle(selectedNavigationState == destination ? QuizzlerTheme.primaryCyan : QuizzlerTheme.textMuted)
                        .frame(maxWidth: .infinity, minHeight: QuizzlerTheme.minimumTouchTarget)
                }
                .accessibilityLabel(destination.title)
                .accessibilityValue(selectedNavigationState == destination ? "Selected" : "Not selected")
                .accessibilityAddTraits(selectedNavigationState == destination ? [.isSelected] : [])
            }
        }
        .padding(.horizontal, 4)
        .background(QuizzlerTheme.elevatedCard.opacity(0.75))
    }

    private var selectedNavigationState: LaunchpadState {
        switch state {
        case .question, .feedback, .results: .today
        default: state
        }
    }

    private func isCorrect(_ question: StudyQuestion) -> Bool {
        QuestionShellView.correctAnswer(for: question.question, selection: selection)
    }

    private func startSession() {
        selection = .none
        sessionIndex = resumeIndex(count: catalog.questions.count)
        state = .question
    }

    private func selectCourse(_ packKey: String) {
        guard catalog.select(packKey: packKey) else { return }
        // A session belongs to the old pack. Returning to Today is clearer
        // than carrying a numeric position into a newly selected course.
        sessionIndex = nil
        selection = .none
        state = .today
    }

    private func checkAnswer(_: Bool) {
        guard let question = currentQuestion else { return }
        progress.recordAndSave(.init(identity: question.identity, correct: isCorrect(question)))
        state = .feedback
    }

    private func finishQuestion() {
        // Save while the answered item remains pinned, then keep the review
        // session on the next pack question. The pin prevents an async save
        // from changing the feedback item before this transition completes.
        let questionCount = catalog.questions.count
        guard questionCount > 0 else { return }
        progress.saveCurrentSession()
        selection = .none
        let currentIndex = sessionIndex ?? resumeIndex(count: questionCount)
        let nextIndex = (currentIndex + 1) % questionCount
        sessionIndex = nextIndex
        if let pack = catalog.pack {
            StudyResumePosition.store(
                nextIndex,
                courseID: pack.courseID,
                packID: pack.packID,
                questionCount: questionCount
            )
        }
        state = .question
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
    let onStart: () -> Void
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
                Button("View progress", action: onProgress)
                    .buttonStyle(.bordered)
                    .tint(QuizzlerTheme.primaryCyan)
                    .frame(maxWidth: .infinity, minHeight: 44)
            }
            .padding(QuizzlerTheme.pageGutter)
        }
        .background(QuizzlerTheme.terminalBackground)
    }
}

private struct ResultsView: View {
    let answered: Int
    let correct: Int
    let saving: Bool
    let saveFailed: Bool
    let syncPending: Bool
    let accountChanged: Bool
    let onRetrySave: () -> Void
    let onNext: () -> Void
    let onProgress: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            eyebrow("Results")
            Text("Session complete")
                .font(.largeTitle.weight(.bold))
                .foregroundStyle(QuizzlerTheme.textPrimary)
            Text("\(correct) correct · \(answered) answered")
                .font(.title3)
                .foregroundStyle(QuizzlerTheme.textPrimary)
            if saving {
                Label("Saving progress locally…", systemImage: "arrow.triangle.2.circlepath")
                    .foregroundStyle(QuizzlerTheme.textMuted)
                    .accessibilityLabel("Saving progress locally")
            } else if accountChanged {
                Text("This device has a different iCloud account. Your study history is safe on this device. Sign in to the original account to resume syncing.")
                    .foregroundStyle(QuizzlerTheme.textMuted)
            } else if syncPending {
                Text("Progress is saved on this device. iCloud has not updated yet.")
                    .foregroundStyle(QuizzlerTheme.textMuted)
                Button("Retry sync", action: onRetrySave)
                    .buttonStyle(.bordered)
                    .tint(QuizzlerTheme.primaryCyan)
            } else if saveFailed {
                Text("Progress was not saved. Retry before continuing.")
                    .foregroundStyle(QuizzlerTheme.danger)
                Button("Retry save", action: onRetrySave)
                    .buttonStyle(.bordered)
                    .tint(QuizzlerTheme.primaryCyan)
            }
            Button("Continue review", action: onNext)
                .buttonStyle(.borderedProminent)
                .tint(QuizzlerTheme.primaryCyan)
                .foregroundStyle(.black)
                .frame(maxWidth: .infinity, minHeight: 48)
            Button("View progress", action: onProgress)
                .buttonStyle(.bordered)
                .tint(QuizzlerTheme.primaryCyan)
                .frame(maxWidth: .infinity, minHeight: 44)
            Spacer()
        }
        .padding(QuizzlerTheme.pageGutter)
        .background(QuizzlerTheme.terminalBackground)
    }
}

private struct ProgressView: View {
    let answered: Int
    let correct: Int
    let persistenceState: LaunchpadProgressModel.PersistenceState
    let onRetrySync: () -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                eyebrow("Progress")
                Text("Your study history")
                    .font(.largeTitle.weight(.bold))
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                stat("Answered", value: "\(answered)")
                stat("Correct", value: "\(correct)")
                syncDetail
            }
            .padding(QuizzlerTheme.pageGutter)
        }
        .background(QuizzlerTheme.terminalBackground)
    }

    private func stat(_ label: String, value: String) -> some View {
        HStack {
            Text(label).foregroundStyle(QuizzlerTheme.textMuted)
            Spacer()
            Text(value).font(.title2.monospacedDigit()).foregroundStyle(QuizzlerTheme.primaryCyan)
        }
        .padding(16)
        .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
    }

    @ViewBuilder private var syncDetail: some View {
        switch persistenceState {
        case .synced:
            Label("Progress is synced through iCloud.", systemImage: "checkmark.icloud")
                .font(.subheadline)
                .foregroundStyle(QuizzlerTheme.textMuted)
        case .syncing:
            Label("Syncing progress with iCloud…", systemImage: "arrow.triangle.2.circlepath")
                .font(.subheadline)
                .foregroundStyle(QuizzlerTheme.textMuted)
                .accessibilityLabel("Syncing progress with iCloud")
        case .syncPending:
            VStack(alignment: .leading, spacing: 8) {
                Text("Progress is saved on this device. iCloud needs another try.")
                    .font(.subheadline)
                    .foregroundStyle(QuizzlerTheme.textMuted)
                Button("Retry sync", action: onRetrySync)
                    .buttonStyle(.bordered)
                    .tint(QuizzlerTheme.primaryCyan)
            }
        case .accountChanged:
            Text("This device has a different iCloud account. Your study history is safe here. Sign in to the original account to resume syncing.")
                .font(.subheadline)
                .foregroundStyle(QuizzlerTheme.textMuted)
        case .loading, .local, .saving, .saveFailed:
            Text("Progress is stored on this device.")
                .font(.subheadline)
                .foregroundStyle(QuizzlerTheme.textMuted)
        }
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

private func eyebrow(_ text: String) -> some View {
    Text(text.uppercased())
        .font(QuizzlerTheme.metadataFont)
        .foregroundStyle(QuizzlerTheme.primaryCyan)
}
