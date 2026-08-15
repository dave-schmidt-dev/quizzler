import SwiftUI
import QuizzlerKit

@main
struct QuizzlerApp: App {
    private let progressRepository = QuizzlerProgressRepository.production()

    var body: some Scene {
        WindowGroup {
#if DEBUG
            if let mode = DevelopmentProbeLaunch.mode {
                DevelopmentProbeView(mode: mode)
            } else if UITestFixture.isEnabled {
                UITestFixtureView()
            } else {
                LaunchpadView(repository: progressRepository)
            }
#else
            LaunchpadView(repository: progressRepository)
#endif
        }
    }
}

enum QuizzlerProgressRepository {
    static func production() -> ProgressRepository {
        guard let applicationSupport = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first else {
            preconditionFailure("Application Support is unavailable")
        }
        let fileURL = applicationSupport
            .appendingPathComponent("Quizzler", isDirectory: true)
            .appendingPathComponent("progress-v1.json", isDirectory: false)
        return ProgressRepository(actorID: "local-device", store: LocalProgressStore(fileURL: fileURL))
    }
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
