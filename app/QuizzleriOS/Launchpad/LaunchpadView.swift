import Foundation
import SwiftUI
import QuizzlerKit
import UIKit

struct LaunchpadView: View {
    @Environment(\.scenePhase) private var scenePhase
    @State var state: LaunchpadState = .today
    /// The questions this session will serve, and how far through them we are.
    /// `nil` between sessions, when the position follows from saved progress.
    @State var activeSession: ActiveSession?
    @State var selection: QuestionSelection = .none
    @State var scheduledReviewStates: [QuestionIdentity: SRSState] = [:]
    /// The re-validated saved session Today offers to continue (C3).
    @State var resumable: SessionResume.ResumableSession?
    /// Per-question answered counts captured once when a session begins and
    /// reused by every later save, so resume can detect answers made
    /// elsewhere while the session was saved (C3).
    @State var sessionStartBaseline: [BaselineEntry] = []
    let repository: any LaunchpadProgressRepository
    /// Device-local persistence for in-progress sessions (C3).
    let sessionStore: ActiveSessionStore
    @StateObject var progress: LaunchpadProgressModel
    @StateObject var catalog: StudyCatalogModel
#if targetEnvironment(macCatalyst)
    @StateObject private var issueInbox = IssueInboxModel()
#endif

    /// The durable default chosen in Settings.
    @AppStorage(StudySessionLength.key) private var storedSessionLength = StudySessionLength.default
    @AppStorage(StudyScheduledReview.key) var scheduledReviewEnabled = StudyScheduledReview.default
    /// A Today choice applies once. Settings remains the durable source of
    /// truth, so changing this value cannot silently alter future sessions.
    @State var nextSessionLengthOverride: Int?
    @State var showingCourses = false
    @State var showingLab = false

    init(
        repository: any LaunchpadProgressRepository,
        catalog: StudyCatalogModel = StudyCatalogModel(),
        sessionStore: ActiveSessionStore = ActiveSessionStore(fileURL: ActiveSessionStore.defaultFileURL)
    ) {
        self.repository = repository
        self.sessionStore = sessionStore
        _progress = StateObject(wrappedValue: LaunchpadProgressModel(repository: repository))
        _catalog = StateObject(wrappedValue: catalog)
#if targetEnvironment(macCatalyst)
        _issueInbox = StateObject(wrappedValue: IssueInboxModel())
#endif
    }

    /// `nil` until a pack is installed and decoded. Every study screen is
    /// gated on this rather than falling back to built-in content: an app with
    /// no packs must look empty, not look like a very short course.
    var currentQuestion: StudyQuestion? {
        let questions = catalog.questions
        guard !questions.isEmpty else { return nil }
        if let session = activeSession {
            guard session.position < session.questions.count else { return nil }
            return session.questions[session.position]
        }
        return questions[resumeIndex(count: questions.count)]
    }

    /// True while the session sits on its last question, so the question
    /// shell's primary button can say "Finish session" instead of "Next
    /// question" (C6).
    private var isLastSessionQuestion: Bool {
        guard let session = activeSession else { return false }
        return session.position == session.questions.count - 1
    }

    /// The actions and enabled-state the Mac's Session menu commands follow
    /// for the focused scene (C4). Rebuilt every render so the menu tracks
    /// the session's current question.
    private var sessionCommandsValue: SessionCommandsValue {
        SessionCommandsValue(
            availability: SessionCommandAvailability(
                state: state,
                isFeedback: state == .feedback,
                hasSelection: !selection.isEmpty,
                isLastQuestion: isLastSessionQuestion
            ),
            primary: {
                if state == .feedback {
                    finishQuestion()
                } else if let question = currentQuestion {
                    checkAnswer(isCorrect(question))
                }
            },
            skip: skipQuestion,
            end: endSession
        )
    }

    func resumeIndex(count: Int) -> Int {
        guard let pack = catalog.pack else { return 0 }
        return StudyResumePosition.index(
            courseID: pack.courseID,
            packID: pack.packID,
            questionCount: count
        )
    }

    var currentInsights: StudyInsights {
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

    var effectiveSessionLength: Int {
        StudySessionLength.effective(
            stored: storedSessionLength,
            nextSessionOverride: nextSessionLengthOverride
        )
    }

    func sessionLimit(candidateCount: Int) -> Int {
        StudySessionLength.limit(
            stored: storedSessionLength,
            nextSessionOverride: nextSessionLengthOverride,
            candidateCount: candidateCount
        )
    }

    /// Clear the one-time Today selection only after a valid session has been
    /// created. A failed or empty launch leaves the learner's next choice intact.
    func consumeNextSessionLengthOverride() {
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
                .sheet(isPresented: $showingLab) {
                    QuietPowerShellLabView()
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
                    partialInstall: catalog.pack?.partialInstall,
                    scheduledReviewEnabled: scheduledReviewEnabled,
                    persistenceState: progress.persistenceState,
                    onRetrySync: progress.saveCurrentSession,
                    onStartDueReview: startDueReview,
                    onStartRetryMissed: startRetryMissed,
                    onRefresh: progress.synchronizeOnForeground
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
        .environment(\.sessionIsLastQuestion, isLastSessionQuestion)
        .focusedSceneValue(\.sessionCommands, sessionCommandsValue)
#if targetEnvironment(macCatalyst)
        .safeAreaInset(edge: .bottom, spacing: 0) {
            // Same rule as the phone: no tab bar inside a session.
            if CatalystTabBarPolicy.isVisible(for: state) {
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
        .task(id: resumeRefreshKey) {
            guard resumeRefreshKey != nil else { return }
            refreshResumeCandidate()
        }
        .onChange(of: scenePhase) { _, newPhase in
            // Leaving the foreground mid-session must not lose the plan (C3).
            if newPhase == .background || newPhase == .inactive {
                persistSession()
            }
            guard newPhase == .active else { return }
            progress.synchronizeOnForeground()
#if targetEnvironment(macCatalyst)
            issueInbox.refresh()
#endif
        }
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

extension View {
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
