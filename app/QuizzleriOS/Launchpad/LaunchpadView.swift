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
        case saveFailed
    }

    @Published private(set) var aggregate = AggregateSnapshot()
    @Published private(set) var unsavedAnswers: [SessionAnswer] = []
    @Published private(set) var persistenceState: PersistenceState = .loading

    private let repository: any LaunchpadProgressRepository
    private let beforeSave: @Sendable () async -> Void

    init(repository: any LaunchpadProgressRepository, beforeSave: @escaping @Sendable () async -> Void = {}) {
        self.repository = repository
        self.beforeSave = beforeSave
    }

    var answered: Int { aggregate.answered + unsavedAnswers.count }
    var correct: Int { aggregate.correct + unsavedAnswers.filter(\.correct).count }

    func load() {
        persistenceState = .loading
        Task {
            do {
                aggregate = try await repository.snapshot().aggregate
                persistenceState = .local
            } catch {
                persistenceState = .saveFailed
            }
        }
    }

    func record(_ answer: SessionAnswer) {
        unsavedAnswers.append(answer)
    }

    func saveCurrentSession() {
        guard persistenceState != .saving else { return }
        guard !unsavedAnswers.isEmpty else {
            if persistenceState == .saveFailed {
                load()
            }
            return
        }
        persistNextBatch()
    }

    private func persistNextBatch() {
        guard !unsavedAnswers.isEmpty else {
            persistenceState = .local
            return
        }
        let batch = Array(unsavedAnswers)
        persistenceState = .saving
        Task {
            do {
                await beforeSave()
                _ = try await repository.save(SessionDetail(answers: batch))
                unsavedAnswers.removeFirst(batch.count)
                aggregate = try await repository.snapshot().aggregate
                if unsavedAnswers.isEmpty {
                    persistenceState = .local
                } else {
                    persistNextBatch()
                }
            } catch {
                persistenceState = .saveFailed
            }
        }
    }
}

protocol LaunchpadProgressRepository: Sendable {
    func snapshot() async throws -> ProgressEnvelope
    func save(_ session: SessionDetail) async throws -> ProgressOperation
}

extension ProgressRepository: LaunchpadProgressRepository {
    func save(_ session: SessionDetail) async throws -> ProgressOperation {
        try await save(session, operationID: nil, now: Date())
    }
}

struct LaunchpadView: View {
    @State private var state: LaunchpadState = .today
    /// The question the current session is showing. `nil` between sessions,
    /// when the position follows from saved progress instead. It is pinned for
    /// the duration of a session so recording an answer cannot swap the
    /// question out from under the Feedback screen.
    @State private var sessionIndex: Int?
    @State private var selection: QuestionSelection = .none
    private let repository: ProgressRepository
    @StateObject private var progress: LaunchpadProgressModel
    @StateObject private var catalog: StudyCatalogModel

    init(repository: ProgressRepository, catalog: StudyCatalogModel = StudyCatalogModel()) {
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
        StudyPosition.resumeIndex(answered: progress.answered, questionCount: count)
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
        case .saveFailed: "local save failed · retry required"
        }
    }

    @ViewBuilder private var content: some View {
        switch state {
        case .progress:
            // Progress and Settings describe the install itself, so they stay
            // reachable when no pack is available to study.
            ProgressView(answered: progress.answered, correct: progress.correct)
        case .settings:
            SettingsView(courseTitle: catalog.courseTitle, packFailures: catalog.failures)
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
                correct: progress.correct,
                answered: progress.answered,
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
                answered: progress.answered,
                correct: progress.correct,
                saving: progress.persistenceState == .saving,
                saveFailed: progress.persistenceState == .saveFailed,
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

    private func checkAnswer(_: Bool) {
        guard let question = currentQuestion else { return }
        progress.record(.init(identity: question.identity, correct: isCorrect(question)))
        state = .feedback
    }

    private func finishQuestion() {
        // Releasing the pin is all that advances the course: the next position
        // comes from the answer just recorded, so it survives a relaunch.
        sessionIndex = nil
        progress.saveCurrentSession()
        state = .results
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

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                eyebrow("Progress")
                Text("Your study history")
                    .font(.largeTitle.weight(.bold))
                    .foregroundStyle(QuizzlerTheme.textPrimary)
                stat("Answered", value: "\(answered)")
                stat("Correct", value: "\(correct)")
                Text("Progress is stored locally on this device. Cloud sharing remains unavailable until Production qualification.")
                    .font(.subheadline)
                    .foregroundStyle(QuizzlerTheme.textMuted)
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
}

private struct SettingsView: View {
    let courseTitle: String
    let packFailures: [PackLoadFailure]

    var body: some View {
        Form {
            Section("Study") {
                LabeledContent("Course", value: courseTitle)
                LabeledContent("App version", value: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1.0.0")
                LabeledContent("Progress", value: "Local only")
            }
            if !packFailures.isEmpty {
                // A pack that was bundled but refused is reported here rather
                // than dropped, so the course going missing has a stated cause.
                Section("Packs not loaded") {
                    ForEach(packFailures, id: \.path) { failure in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(failure.path).font(.subheadline.weight(.semibold))
                            Text(failure.reason).font(.caption).foregroundStyle(QuizzlerTheme.textMuted)
                        }
                    }
                }
            }
            Section("About") {
                Text("Question packs stay on this device. Reports include question context only.")
            }
        }
        .scrollContentBackground(.hidden)
        .background(QuizzlerTheme.terminalBackground)
        .foregroundStyle(QuizzlerTheme.textPrimary)
    }
}

private func eyebrow(_ text: String) -> some View {
    Text(text.uppercased())
        .font(QuizzlerTheme.metadataFont)
        .foregroundStyle(QuizzlerTheme.primaryCyan)
}
