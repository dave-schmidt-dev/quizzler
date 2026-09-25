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
    let repository: any LaunchpadProgressRepository
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
    var currentQuestion: StudyQuestion? {
        let questions = catalog.questions
        guard !questions.isEmpty else { return nil }
        if let session = activeSession {
            guard session.position < session.questions.count else { return nil }
            return session.questions[session.position]
        }
        return questions[resumeIndex(count: questions.count)]
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
