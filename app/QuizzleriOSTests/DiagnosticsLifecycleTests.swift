import AppDiagnostics
import Darwin
import Foundation
import QuizzlerKit
import XCTest
import UIKit
@testable import QuizzleriOS

@MainActor
final class DiagnosticsLifecycleTests: XCTestCase {
    func testProcessNotificationLaunchOnceAndBackgroundAdmissionRemainsOpen() async throws {
        guard let resolved = realpath(NSTemporaryDirectory(), nil) else { throw DiagnosticsError.io }
        defer { free(resolved) }
        let root = URL(fileURLWithPath: String(cString: resolved)).appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(at: root) }
        let identity = try AppIdentity(project: "quizzler", bundleID: "com.zerodelta.quizzler",
            platform: QuizzlerDiagnostics.platform, appVersion: "1.0", build: "1")
        let adapter = try QuizzlerDiagnostics(configuration: DiagnosticsConfiguration(identity: identity, directory: root.appendingPathComponent("spool")))
        var registrations = 0
        let notifications = CountingDiagnosticsNotificationCenter()
        let delegate = QuizzlerAppDelegate(registerForRemoteNotifications: { registrations += 1 }, diagnostics: adapter,
            diagnosticsNotificationCenter: notifications)
        XCTAssertTrue(delegate.application(UIApplication.shared, didFinishLaunchingWithOptions: nil))
        XCTAssertTrue(delegate.application(UIApplication.shared, didFinishLaunchingWithOptions: nil))
        XCTAssertEqual(registrations, 1)
        XCTAssertEqual(notifications.backgroundRegistrations, 1)
        await adapter.lifecycle(.start)
        let initialFlush = await adapter.flush()
        XCTAssertEqual(initialFlush, .persisted)
        let initialized = await adapter.status()
        XCTAssertNotNil(initialized)
        XCTAssertEqual(initialized?.runLoss.admittedCount, 1)
        let gate = DiagnosticsFlushGate()
        delegate.diagnosticsBeforeBackgroundFlush = { await gate.pauseOnce() }
        // Posting the same process notification UIKit sends exercises the real
        // registration, including repeated launch and overlapping delivery.
        notifications.post(name: UIApplication.didEnterBackgroundNotification, object: nil)
        XCTAssertNotNil(delegate.diagnosticsBackgroundTask)
        for _ in 0..<1000 {
            if gate.reached { break }
            await Task.yield()
        }
        XCTAssertTrue(gate.reached)
        notifications.post(name: UIApplication.didEnterBackgroundNotification, object: nil)
        let worker = delegate.diagnosticsBackgroundTask
        // One pending bit retains a later fact while the sole worker is active.
        XCTAssertTrue(delegate.diagnosticsBackgroundPending)
        gate.release()
        await worker?.value
        let flushed = await adapter.flush()
        XCTAssertEqual(flushed, .persisted)
        let status = await adapter.status()
        XCTAssertEqual(status?.closed, false)
        XCTAssertEqual(status?.runLoss.admittedCount, 3)
        XCTAssertNil(delegate.diagnosticsBackgroundTask)
        notifications.post(name: UIApplication.didEnterBackgroundNotification, object: nil)
        await delegate.diagnosticsBackgroundTask?.value
        let nextStatus = await adapter.status()
        XCTAssertEqual(nextStatus?.runLoss.admittedCount, (status?.runLoss.admittedCount ?? 0) + 1)
        XCTAssertEqual(nextStatus?.closed, false)
        #if targetEnvironment(macCatalyst)
        XCTAssertEqual(QuizzlerDiagnostics.platform, .macos)
        XCTAssertEqual(QuizzlerDiagnostics.protectionPolicy, "privatePOSIX")
        #else
        XCTAssertEqual(QuizzlerDiagnostics.platform, .ios)
        XCTAssertEqual(QuizzlerDiagnostics.protectionPolicy, "completeUntilFirstUserAuthentication")
        #endif
    }

    func testAppSettledFixturePolicyUsesSharedPredicateForEveryAlias() {
        let aliases = ["--quizzler-ui-test-launch-sting-settled", "--quizzler-launch-sting-settled", "--launch-sting-settled"]
        for alias in aliases {
            let arguments = ["App", alias]
            XCTAssertTrue(ColdLaunchStingPolicy.isSettledFixtureLaunch(arguments: arguments, environment: [:]))
            XCTAssertEqual(ColdLaunchStingPolicy.isSettledFixtureLaunch(arguments: arguments, environment: [:]),
                QuizzlerDiagnostics.isSettledFixtureLaunch(arguments: arguments, environment: [:]))
        }
        for key in ["QUIZZLER_UI_TEST_LAUNCH_STING_SETTLED", "QUIZZLER_LAUNCH_STING_SETTLED"] {
            XCTAssertTrue(ColdLaunchStingPolicy.isSettledFixtureLaunch(arguments: ["App"], environment: [key: "enabled"]))
        }
        XCTAssertFalse(ColdLaunchStingPolicy.isSettledFixtureLaunch(arguments: ["--launch-sting-settled"], environment: [:]))
        XCTAssertFalse(ColdLaunchStingPolicy.isSettledFixtureLaunch(arguments: ["/fixture/App", "--ordinary"], environment: [:]))
    }

    func testRejectedDiagnosticFactDoesNotHideAccountIsolationOrRetrySync() async throws {
        guard let resolved = realpath(NSTemporaryDirectory(), nil) else { throw DiagnosticsError.io }
        defer { free(resolved) }
        let root = URL(fileURLWithPath: String(cString: resolved)).appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(at: root) }
        let identity = try AppIdentity(project: "quizzler", bundleID: "com.zerodelta.quizzler",
            platform: QuizzlerDiagnostics.platform, appVersion: "1.0", build: "1")
        let adapter = try QuizzlerDiagnostics(configuration: DiagnosticsConfiguration(identity: identity, directory: root.appendingPathComponent("spool")))
        let repository = ControlledProgressRepository(syncMode: .cloudKit)
        let model = LaunchpadProgressModel(repository: repository, diagnostics: adapter)
        model.load()
        for _ in 0..<1000 {
            if model.persistenceState == .synced { break }
            await Task.yield()
        }
        XCTAssertEqual(model.persistenceState, .synced)
        await repository.waitForStatusStream()
        await repository.emitStatus(.init(state: .accountIsolationRequired, reason: .accountChanged,
            pendingOperationCount: Int.max, pendingIssueCount: 1))
        for _ in 0..<1000 {
            if model.persistenceState == .accountChanged { break }
            await Task.yield()
        }
        XCTAssertEqual(model.persistenceState, .accountChanged)
        let unavailable = await adapter.unavailable
        XCTAssertEqual(unavailable, .invalidFact)
        let calls = await repository.synchronizeCallCount()
        XCTAssertEqual(calls, 1)
        let flushed = await adapter.flush()
        XCTAssertEqual(flushed, .persisted)
        let status = await adapter.status()
        XCTAssertEqual(status?.runLoss.admittedCount, 0)
    }
}

/// NotificationCenter is thread safe; this subclass protects its sole mutable
/// registration counter with a lock and delegates all delivery to Foundation.
private final class CountingDiagnosticsNotificationCenter: NotificationCenter, @unchecked Sendable {
    private let lock = NSLock()
    private var registrations = 0
    var backgroundRegistrations: Int {
        lock.lock()
        defer { lock.unlock() }
        return registrations
    }

    override func addObserver(_ observer: Any, selector: Selector, name: NSNotification.Name?, object: Any?) {
        if name == UIApplication.didEnterBackgroundNotification {
            lock.lock()
            registrations += 1
            lock.unlock()
        }
        super.addObserver(observer, selector: selector, name: name, object: object)
    }
}

@MainActor
private final class DiagnosticsFlushGate {
    private var continuation: CheckedContinuation<Void, Never>?
    private(set) var reached = false
    func pauseOnce() async {
        guard !reached else { return }
        reached = true
        await withCheckedContinuation { continuation = $0 }
    }
    func release() { continuation?.resume(); continuation = nil }
}
