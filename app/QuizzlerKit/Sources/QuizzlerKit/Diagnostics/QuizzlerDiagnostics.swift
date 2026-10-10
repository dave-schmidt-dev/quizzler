import AppDiagnostics
import Darwin
import Foundation
import OSLog

public enum QuizzlerDiagnosticsUnavailable: String, Sendable {
    case identity, isolatedRun, storage, invalidFact, admission
}

public enum QuizzlerDiagnosticsLifecycle: Sendable { case start, background }

/// Private keyless capture; construction performs no filesystem work. This actor owns
/// setup and fixed fact mapping; the SDK owns all spool writes and bounded admission.
public actor QuizzlerDiagnostics {
    public static let shared = QuizzlerDiagnostics()
    public static var platform: Platform {
        #if targetEnvironment(macCatalyst) || os(macOS)
        .macos
        #else
        .ios
        #endif
    }

    public static var protectionPolicy: String {
        #if os(iOS) && !targetEnvironment(macCatalyst)
        "completeUntilFirstUserAuthentication"
        #else
        "privatePOSIX"
        #endif
    }

    private let configuration: DiagnosticsConfiguration?
    private let isolated: Bool
    private let warningObserver: (@Sendable () -> Void)?
    private var logger: DiagnosticsLogger?
    private var initialized = false
    private var started = false
    private var warned = false
    /// Latched refusal history; later valid facts may still be admitted.
    public private(set) var unavailable: QuizzlerDiagnosticsUnavailable?
    public private(set) var lastAdmission: AdmissionResult?
    public private(set) var refusedAdmissions = 0
    private static let fallback = Logger(subsystem: "com.zerodelta.quizzler", category: "diagnostics")

    public init(configuration: DiagnosticsConfiguration? = nil) {
        self.init(configuration: configuration, isolated: Self.isolatedProcess(), warningObserver: nil)
    }

    /// Shared construction path keeps production initialization and warning observation consistent.
    init(configuration: DiagnosticsConfiguration?, isolated: Bool,
         warningObserver: (@Sendable () -> Void)? = nil) {
        self.configuration = configuration
        self.isolated = isolated
        self.warningObserver = warningObserver
    }

    private static func isolatedProcess() -> Bool {
        let environment = ProcessInfo.processInfo.environment
        return ProcessInfo.processInfo.processName == "swiftpm-testing-helper"
            || NSClassFromString("XCTestCase") != nil || NSClassFromString("XCTest.XCTestCase") != nil
            || environment["XCTestConfigurationFilePath"] != nil
            || environment.keys.contains { $0.hasPrefix("QUIZZLER_UI_TEST") }
            || Self.isSettledFixtureLaunch(arguments: CommandLine.arguments, environment: environment)
            || Self.hasIsolatedArguments(CommandLine.arguments)
    }

    /// Project-local fixture policy shared with the app launch policy. Arguments
    /// are a complete process argument vector; the executable is never a flag.
    public static func isSettledFixtureLaunch(arguments: [String], environment: [String: String]) -> Bool {
        let flags: Set<String> = ["--quizzler-ui-test-launch-sting-settled",
            "--quizzler-launch-sting-settled", "--launch-sting-settled"]
        return arguments.dropFirst().contains { flags.contains($0) }
            || environment["QUIZZLER_UI_TEST_LAUNCH_STING_SETTLED"] == "enabled"
            || environment["QUIZZLER_LAUNCH_STING_SETTLED"] == "enabled"
    }

    static func hasIsolatedArguments(_ arguments: [String]) -> Bool {
        let probes: Set<String> = ["--quizzler-development-cloudkit-probe", "--quizzler-development-cloudkit-probe-recover"]
        return isSettledFixtureLaunch(arguments: arguments, environment: [:])
            || arguments.dropFirst().contains { probes.contains($0) }
    }

    /// Exact production bundle identity only; invalid metadata remains unavailable.
    public static func identity(bundleID: String?, version: String?, build: String?) -> AppIdentity? {
        guard bundleID == "com.zerodelta.quizzler", let version, let build else { return nil }
        return try? AppIdentity(project: "quizzler", bundleID: "com.zerodelta.quizzler",
                                platform: platform, appVersion: version, build: build)
    }

    private func warn(_ reason: QuizzlerDiagnosticsUnavailable) {
        unavailable = reason
        guard !warned else { return }
        warned = true
        if let warningObserver { warningObserver() }
        else { Self.fallback.warning("quizzler-diagnostics-refused") }
    }

    private func initialize() {
        guard !initialized else { return }
        initialized = true
        if let configuration {
            logger = DiagnosticsLogger(configuration: configuration)
            return
        }
        guard !isolated else { unavailable = .isolatedRun; return }
        let bundle = Bundle.main
        guard let identity = Self.identity(bundleID: bundle.bundleIdentifier,
            version: bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String,
            build: bundle.object(forInfoDictionaryKey: "CFBundleVersion") as? String) else {
            warn(.identity); return
        }
        do {
            guard let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else {
                warn(.storage); return
            }
            let parent = support.appendingPathComponent("QuizzlerDiagnostics", isDirectory: true)
            var attributes: [FileAttributeKey: Any] = [.posixPermissions: 0o700]
            #if os(iOS) && !targetEnvironment(macCatalyst)
            attributes[.protectionKey] = FileProtectionType.completeUntilFirstUserAuthentication
            #endif
            try Self.prepareDirectory(parent, attributes: attributes, intermediates: true, excludeFromBackup: true)
            guard let resolved = realpath(parent.path, nil) else { warn(.storage); return }
            defer { free(resolved) }
            let directory = URL(fileURLWithPath: String(cString: resolved)).appendingPathComponent("spool", isDirectory: true)
            try Self.prepareDirectory(directory, attributes: attributes, intermediates: false)
            logger = try DiagnosticsLogger(configuration: DiagnosticsConfiguration(identity: identity, directory: directory))
        } catch { warn(.storage) }
    }

    /// Existing directory ambiguity is refused, never chmod-repaired through a symlink.
    static func prepareDirectory(_ directory: URL, attributes: [FileAttributeKey: Any], intermediates: Bool, excludeFromBackup: Bool = false) throws {
        var info = stat()
        if lstat(directory.path, &info) != 0 {
            guard errno == ENOENT else { throw DiagnosticsError.unsafeStorage }
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: intermediates, attributes: attributes)
            guard lstat(directory.path, &info) == 0 else { throw DiagnosticsError.unsafeStorage }
        }
        guard info.st_mode & S_IFMT == S_IFDIR, info.st_uid == geteuid(), info.st_mode & 0o777 == 0o700 else {
            throw DiagnosticsError.unsafeStorage
        }
        #if os(iOS) && !targetEnvironment(macCatalyst)
        let existing = try FileManager.default.attributesOfItem(atPath: directory.path)
        let protection = (existing[.protectionKey] as? FileProtectionType)?.rawValue
            ?? (existing[.protectionKey] as? String)
        guard protection == FileProtectionType.completeUntilFirstUserAuthentication.rawValue else {
            throw DiagnosticsError.unsafeStorage
        }
        #endif
        if excludeFromBackup {
            var directory = directory
            var values = URLResourceValues()
            values.isExcludedFromBackup = true
            try directory.setResourceValues(values)
            guard try directory.resourceValues(forKeys: [.isExcludedFromBackupKey]).isExcludedFromBackup == true else {
                throw DiagnosticsError.unsafeStorage
            }
        }
    }

    private func admit(_ event: DiagnosticEvent) {
        guard let logger else { return }
        let result = logger.record(event)
        lastAdmission = result
        if result == .queueFull || result == .failed || result == .closed {
            refusedAdmissions += 1; warn(.admission)
        }
    }

    /// Status describes retry state, never a transport invocation or future success.
    /// No question, learner, account, record, arbitrary string or operation ID is accepted.
    public func record(_ status: SyncStatusEvent) {
        initialize()
        guard logger != nil else { return }
        let queue = status.pendingOperationCount.addingReportingOverflow(status.pendingIssueCount)
        guard !queue.overflow, queue.partialValue <= 1_000_000,
              status.retryAttempt <= 1_000_000_000,
              status.retryAfterMilliseconds.map({ $0 <= 86_400_000 }) ?? true else { warn(.invalidFact); return }
        let failure = [.failed, .partialFailure, .recoveryRequired, .accountIsolationRequired].contains(status.state)
            || status.reason == .statePersistenceFailed
        var attributes: [Attribute] = [.subsystem(.sync), .code(status.reason == .statePersistenceFailed ? .storageFailure : .unknown),
            .queueDepth(Int64(queue.partialValue)), .count(Int64(status.retryAttempt)),
            .retryable(status.state == .retryScheduled), .offline(status.state == .offline),
            .outcome(failure ? .failure : .unknown)]
        if let delay = status.retryAfterMilliseconds { attributes.append(.durationMilliseconds(Int64(delay))) }
        do { admit(try DiagnosticEvent(severity: failure ? .warn : .info, kind: .diagnostic,
                                      attributes: attributes, component: .sync)) }
        catch { warn(.invalidFact) }
    }

    /// Background is a lifecycle fact, not process termination; admission stays open.
    public func lifecycle(_ value: QuizzlerDiagnosticsLifecycle) {
        initialize()
        if value == .start { guard !started else { return }; started = true }
        do { admit(try DiagnosticEvent(severity: .info, kind: .lifecycle,
            attributes: [.code(value == .start ? .lifecycleStart : .unknown), .subsystem(.lifecycle), .outcome(.unknown)],
            component: .lifecycle)) }
        catch { warn(.invalidFact) }
    }

    public func status() -> DiagnosticsStatus? { logger?.status }

    /// Persists the admitted cohort without sealing capacity needed by a future collector.
    public func flush() async -> PersistenceResult? {
        guard let logger else { return nil }
        let result = await logger.flush(seal: false)
        if result != .persisted { warn(.storage) }
        return result
    }
}
