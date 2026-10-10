import SwiftUI
import UIKit
import QuizzlerKit
import ZeroDeltaSting

enum ColdLaunchStingPhase: Equatable, Sendable {
    case presenting
    case completed
    case skipped
}

enum ColdLaunchStingPolicy {
    static let completionFailSafeNanoseconds: UInt64 = 3_000_000_000

    static func shouldPresent(
        isDevelopmentProbe: Bool,
        isExistingUITestFixture: Bool,
        isRunningUnderXCTest: Bool,
        hasOptedInForUITest: Bool,
        hasDestination: Bool
    ) -> Bool {
        if isDevelopmentProbe || isExistingUITestFixture || hasDestination {
            return false
        }
        if isRunningUnderXCTest {
            return hasOptedInForUITest
        }
        return true
    }

#if DEBUG
    static func isSettledFixtureLaunch(
        arguments: [String] = ProcessInfo.processInfo.arguments,
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) -> Bool {
        QuizzlerDiagnostics.isSettledFixtureLaunch(arguments: arguments, environment: environment)
    }

    static func isOptedInForUITest(
        arguments: [String] = ProcessInfo.processInfo.arguments,
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) -> Bool {
        arguments.contains("--quizzler-cold-launch-sting")
            || arguments.contains("--cold-launch-sting")
            || environment["QUIZZLER_COLD_LAUNCH_STING"] == "enabled"
    }
#endif
}

private struct ColdLaunchStingSurface: View {
    @Environment(\.colorScheme) private var colorScheme
    var startSettled: Bool = false
    var onFinished: (() -> Void)?

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                ZeroDeltaPalette.background(colorScheme)
                    .ignoresSafeArea()

                ZeroDeltaSting(
                    width: min(geometry.size.width * 0.7, 360),
                    startSettled: startSettled,
                    onFinished: onFinished
                )
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("Zero Delta launch")
                .accessibilityIdentifier("launch.zero-delta-sting")
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .ignoresSafeArea()
    }
}

@MainActor
final class QuizzlerAppDelegate: NSObject, UIApplicationDelegate, ObservableObject {
    @Published internal(set) var coldLaunchStingPhase: ColdLaunchStingPhase

    private let registerForRemoteNotifications: (() -> Void)?
    private var registrationStarted = false
    let diagnostics: QuizzlerDiagnostics
    let diagnosticsNotificationCenter: NotificationCenter
    var diagnosticsStarted = false
    var diagnosticsBackgroundTask: Task<Void, Never>?
    var diagnosticsBackgroundPending = false
    var diagnosticsBeforeBackgroundFlush: (@MainActor () async -> Void)?

    override init() {
        self.diagnostics = .shared
        self.diagnosticsNotificationCenter = .default
        self.registerForRemoteNotifications = nil
        self.coldLaunchStingPhase = Self.initialColdLaunchStingPhase()
        super.init()
    }

    @nonobjc
    init(registerForRemoteNotifications: @escaping () -> Void, diagnostics: QuizzlerDiagnostics = .shared,
         diagnosticsNotificationCenter: NotificationCenter = .default) {
        self.diagnostics = diagnostics
        self.diagnosticsNotificationCenter = diagnosticsNotificationCenter
        self.registerForRemoteNotifications = registerForRemoteNotifications
        self.coldLaunchStingPhase = Self.initialColdLaunchStingPhase()
        super.init()
    }

    private static func initialColdLaunchStingPhase() -> ColdLaunchStingPhase {
#if DEBUG
        let isProbe = DevelopmentProbeLaunch.mode != nil
        let isFixture = UITestFixture.isEnabled || ColdLaunchStingPolicy.isSettledFixtureLaunch()
        let isUnderXCTest = UITestFixture.isRunningUnderXCTest
        let isOptIn = ColdLaunchStingPolicy.isOptedInForUITest()
#else
        let isProbe = false
        let isFixture = false
        let isUnderXCTest = false
        let isOptIn = false
#endif
        if ColdLaunchStingPolicy.shouldPresent(
            isDevelopmentProbe: isProbe,
            isExistingUITestFixture: isFixture,
            isRunningUnderXCTest: isUnderXCTest,
            hasOptedInForUITest: isOptIn,
            hasDestination: false
        ) {
            return .presenting
        } else {
            return .skipped
        }
    }

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        if launchOptions?[.url] != nil || launchOptions?[.remoteNotification] != nil {
            bypassColdLaunchStingForDestination()
        }

        recordDiagnosticsStart()
        guard !registrationStarted else { return true }
        registrationStarted = true
#if DEBUG
        if registerForRemoteNotifications == nil {
            guard !UITestFixture.usesLocalProgress else { return true }
        }
#endif
        if let registerForRemoteNotifications {
            registerForRemoteNotifications()
        } else {
            application.registerForRemoteNotifications()
        }
        return true
    }

    func bypassColdLaunchStingForDestination() {
        guard coldLaunchStingPhase == .presenting else { return }
        coldLaunchStingPhase = .skipped
    }

    func finishColdLaunchSting() {
        guard coldLaunchStingPhase == .presenting else { return }
        coldLaunchStingPhase = .completed
    }
}

@main
struct QuizzlerApp: App {
    @UIApplicationDelegateAdaptor(QuizzlerAppDelegate.self) private var appDelegate
    private let progressRepository: (any LaunchpadProgressRepository)?
    private let launchStingSettledFixture: Bool
    private let sessionStore: ActiveSessionStore

    init() {
#if DEBUG
        // Before any view reads them: a preferences UI test asks for the
        // study defaults to be cleared at launch, and by the time `body`
        // runs it is already too late.
        UITestFixture.resetIsolatedPreferencesAtLaunch()
        let isSettled = ColdLaunchStingPolicy.isSettledFixtureLaunch()
        launchStingSettledFixture = isSettled
        if DevelopmentProbeLaunch.mode != nil || UITestFixture.isEnabled || isSettled {
            progressRepository = nil
        } else {
            progressRepository = QuizzlerProgressRepository.debug()
        }
        sessionStore = Self.makeLaunchpadSessionStore()
#else
        launchStingSettledFixture = false
        progressRepository = QuizzlerProgressRepository.production()
        sessionStore = ActiveSessionStore(fileURL: ActiveSessionStore.defaultFileURL)
#endif
    }

#if DEBUG
    /// While either offline fixture repository is active, saved sessions live
    /// in the UI-test file rather than the learner's real one, and that file
    /// is deleted at launch unless the test asked to keep it across a
    /// relaunch (C3).
    private static func makeLaunchpadSessionStore() -> ActiveSessionStore {
        guard UITestFixture.usesIsolatedActiveSessionStore else {
            return ActiveSessionStore(fileURL: ActiveSessionStore.defaultFileURL)
        }
        UITestFixture.deleteIsolatedActiveSessionStoreAtLaunch()
        return ActiveSessionStore(fileURL: UITestFixture.activeSessionStoreFileURL())
    }
#endif

    var body: some Scene {
        WindowGroup {
            QuizzlerSceneRootView(
                appDelegate: appDelegate,
                progressRepository: progressRepository,
                launchStingSettledFixture: launchStingSettledFixture,
                sessionStore: sessionStore
            )
        }
        // The Mac's Session menu (C4) lists the session actions for the
        // focused scene. The keys themselves (Return, S, Escape) stay on the
        // view-level shortcuts, so the menu adds no second binding.
        .commands { SessionCommands() }
    }
}

private struct QuizzlerSceneRootView: View {
    @ObservedObject var appDelegate: QuizzlerAppDelegate
    let progressRepository: (any LaunchpadProgressRepository)?
    let launchStingSettledFixture: Bool
    let sessionStore: ActiveSessionStore

    var body: some View {
        ZStack {
            Group {
#if DEBUG
                if let mode = DevelopmentProbeLaunch.mode {
                    DevelopmentProbeView(mode: mode)
                } else if launchStingSettledFixture {
                    ColdLaunchStingSurface(startSettled: true)
                } else if UITestFixture.isEnabled {
                    UITestFixtureView()
                } else if let progressRepository {
                    LaunchpadView(
                        repository: progressRepository,
                        catalog: Self.makeLaunchpadCatalog(),
                        sessionStore: sessionStore
                    )
                }
#else
                if let progressRepository {
                    LaunchpadView(repository: progressRepository)
                }
#endif
            }

            if appDelegate.coldLaunchStingPhase == .presenting {
                ColdLaunchStingSurface(
                    startSettled: false,
                    onFinished: appDelegate.finishColdLaunchSting
                )
                // Fade out at --duration-base under the standard ease, never a hard cut.
                .transition(.opacity)
            }
        }
        .animation(.timingCurve(0.2, 0, 0.2, 1, duration: 0.18), value: appDelegate.coldLaunchStingPhase)
#if DEBUG
        .statusBarHidden(appDelegate.coldLaunchStingPhase == .presenting || launchStingSettledFixture)
#else
        .statusBarHidden(appDelegate.coldLaunchStingPhase == .presenting)
#endif
        .onOpenURL { _ in
            appDelegate.bypassColdLaunchStingForDestination()
        }
        .task(id: appDelegate.coldLaunchStingPhase) {
            guard appDelegate.coldLaunchStingPhase == .presenting else { return }
            do {
                try await Task.sleep(nanoseconds: ColdLaunchStingPolicy.completionFailSafeNanoseconds)
            } catch {
                return
            }
            guard !Task.isCancelled, appDelegate.coldLaunchStingPhase == .presenting else { return }
            appDelegate.finishColdLaunchSting()
        }
    }

#if DEBUG
    /// While the offline UI-test fixtures are active, a test can ask for a
    /// synthetic pack by count so preference assertions see deterministic
    /// content instead of whatever this build bundled. Anywhere else the
    /// catalog keeps its production loader and store.
    private static func makeLaunchpadCatalog() -> StudyCatalogModel {
        guard UITestFixture.usesLocalProgress,
              let count = SyntheticStudyPack.requestedQuestionCount(
                  environment: ProcessInfo.processInfo.environment
              ) else {
            return StudyCatalogModel()
        }
        return StudyCatalogModel(
            load: SyntheticStudyPack.makeLoader(count: count),
            selectionStore: SyntheticStudyPack.makeSelectionStore()
        )
    }
#endif
}

enum QuizzlerProgressRepository {
    static func production() -> any LaunchpadProgressRepository {
        QuizzlerCloudProgressFactory.make()
    }

#if DEBUG
    static func debug(
        environment: [String: String] = ProcessInfo.processInfo.environment,
        isRunningUnderXCTest: Bool = UITestFixture.isRunningUnderXCTest
    ) -> any LaunchpadProgressRepository {
        if let script = UITestFixture.cloudStatusScript(environment: environment) {
            return cloudStatusFixture(script: script)
        }
        if UITestFixture.usesLocalProgress(
            environment: environment,
            isRunningUnderXCTest: isRunningUnderXCTest
        ) {
            return localForUITest()
        }
        return production()
    }

    static func cloudStatusFixture(script: UITestFixture.CloudStatusScript) -> any LaunchpadProgressRepository {
        guard let applicationSupport = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first else {
            preconditionFailure("Application Support is unavailable")
        }
        let fileURL = applicationSupport
            .appendingPathComponent("Quizzler", isDirectory: true)
            .appendingPathComponent("ui-test-cloud-status-progress-v1.json", isDirectory: false)
        return CloudStatusFixtureProgressRepository(
            actorID: "ui-test-cloud-status-device",
            store: LocalProgressStore(fileURL: fileURL),
            script: script
        )
    }

    static func localForUITest() -> any LaunchpadProgressRepository {
        guard let applicationSupport = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first else {
            preconditionFailure("Application Support is unavailable")
        }
        let fileURL = applicationSupport
            .appendingPathComponent("Quizzler", isDirectory: true)
            .appendingPathComponent("ui-test-progress-v1.json", isDirectory: false)
        return ProgressRepository(
            actorID: "ui-test-device",
            store: LocalProgressStore(fileURL: fileURL)
        )
    }
#endif
}

#if DEBUG
private enum DevelopmentProbeLaunch {
    static let argument = "--quizzler-development-cloudkit-probe"
    static let recoveryArgument = "--quizzler-development-cloudkit-probe-recover"
    static let environmentKey = "QUIZZLER_DEVELOPMENT_CLOUDKIT_PROBE"
    static let environmentValue = "enabled"

    enum Mode {
        case run
        case recover
    }

    static var mode: Mode? {
        guard ProcessInfo.processInfo.environment[environmentKey] == environmentValue else {
            return nil
        }
        if CommandLine.arguments.contains(recoveryArgument) { return .recover }
        return CommandLine.arguments.contains(argument) ? .run : nil
    }
}

@MainActor
private final class DevelopmentProbeViewModel: ObservableObject {
    private let mode: DevelopmentProbeLaunch.Mode
    @Published private(set) var statusLine = DevelopmentProbeViewModel.line(status: "starting", terminal: false)
    @Published private(set) var terminal = false

    init(mode: DevelopmentProbeLaunch.Mode) {
        self.mode = mode
    }

    func run() {
        Task { @MainActor [weak self] in
            guard let self else { return }
            if DevelopmentProbeFailureInjection.isUnavailableEntitlementOrAccountEnabled {
                // Deliberately stop before state-directory creation or any
                // CloudKit object is constructed. This is a local, redacted
                // failure-path check for the signed Development boundary.
                publish("unavailable_entitlement_or_account", terminal: true)
                return
            }
            do {
                let stateDirectory = try Self.stateDirectory()
                let stateStore = CloudSyncEngineStateStore(
                    url: stateDirectory.appendingPathComponent("cksyncengine-state-v1.data", isDirectory: false)
                )
                publish("starting", terminal: false)
                let progress: @Sendable (CloudSyncProbeResult) -> Void = { [weak self] result in
                    Task { @MainActor in
                        self?.publish(result.status, terminal: result.progress == .complete || result.progress == .failed)
                    }
                }
                switch mode {
                case .run:
                    let transport = try CKSyncEngineTransport(
                        containerIdentifier: "iCloud.com.zerodelta.quizzler.dev",
                        stateStore: stateStore,
                        progress: progress
                    )
                    _ = try await transport.runDevelopmentLifecycle(explicitlyEnabled: true)
                case .recover:
                    _ = try await CKSyncEngineTransport.recoverDevelopmentProbe(
                        explicitlyEnabled: true,
                        containerIdentifier: "iCloud.com.zerodelta.quizzler.dev",
                        stateStore: stateStore,
                        progress: progress
                    )
                }
            } catch let error as CloudSyncProbeError {
                publish(Self.safeStatus(for: error), terminal: true)
            } catch {
                publish("probe_failed", terminal: true)
            }
        }
    }

    private func publish(_ status: String, terminal: Bool) {
        statusLine = Self.line(status: status, terminal: terminal)
        self.terminal = terminal
    }

    private static func line(status: String, terminal: Bool) -> String {
        let terminalValue = terminal ? "true" : "false"
        return "{\"kind\":\"cloudkit_development_probe\",\"status\":\"\(status)\",\"terminal\":\(terminalValue)}"
    }

    private static func safeStatus(for error: CloudSyncProbeError) -> String {
        switch error {
        case .unavailableEntitlementOrAccount:
            return "unavailable_entitlement_or_account"
        case .operationTimedOut:
            return "operation_timed_out"
        case .operationCancelled:
            return "operation_cancelled"
        case .statePersistenceFailed:
            return "state_persistence_failed"
        case .disposableZoneCleanupFailed:
            return "disposable_zone_cleanup_failed"
        case .competingWriteFailed:
            return "competing_write_failed"
        case .accountStatusFailed:
            return "account_status_failed"
        case .fetchChangesFailed:
            return "fetch_changes_failed"
        case .sendChangesFailed:
            return "send_changes_failed"
        case .savingZoneFailed:
            return "saving_zone_failed"
        case .savingRecordFailed:
            return "saving_record_failed"
        case .conflictSendFailed:
            return "conflict_send_failed"
        case .conflictFetchFailed:
            return "conflict_fetch_failed"
        case .replaySendFailed:
            return "replay_send_failed"
        case .deletingRecordFailed:
            return "deleting_record_failed"
        case .deletingZoneFailed:
            return "deleting_zone_failed"
        case .conflictNotObserved:
            return "conflict_not_observed"
        case .replayNotAcknowledged:
            return "replay_not_acknowledged"
        case .stateResetFailed:
            return "state_reset_failed"
        case .explicitOptInRequired:
            return "explicit_opt_in_required"
        case .unsupportedPlatform:
            return "unsupported_platform"
        }
    }

    private static func stateDirectory() throws -> URL {
        let directory = try FileManager.default.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        ).appendingPathComponent("QuizzlerDevelopmentProbe", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory
    }
}

private struct DevelopmentProbeView: View {
    @StateObject private var model: DevelopmentProbeViewModel

    init(mode: DevelopmentProbeLaunch.Mode) {
        _model = StateObject(wrappedValue: DevelopmentProbeViewModel(mode: mode))
    }

    var body: some View {
        VStack(spacing: 16) {
            Text("CloudKit Development probe")
                .accessibilityIdentifier("cloudkit-development-probe-title")
            Text(model.statusLine)
                .font(.footnote.monospaced())
                .textSelection(.enabled)
                .accessibilityIdentifier("cloudkit-development-probe-status")
        }
        .padding()
        .task { model.run() }
    }
}
#endif
