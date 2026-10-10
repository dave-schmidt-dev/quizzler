import AppDiagnostics
import Darwin
import Foundation
import Testing
@testable import QuizzlerKit

private func diagnosticsFixture(segmentBytes: Int = 4096, maxSegments: Int = 8) throws -> (URL, DiagnosticsConfiguration) {
    guard let resolved = realpath(NSTemporaryDirectory(), nil) else { throw DiagnosticsError.io }
    defer { free(resolved) }
    let root = URL(fileURLWithPath: String(cString: resolved)).appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
    let identity = try AppIdentity(project: "quizzler", bundleID: "com.zerodelta.quizzler", platform: .macos, appVersion: "1.0", build: "1")
    return try (root, DiagnosticsConfiguration(identity: identity, directory: root.appendingPathComponent("spool"), segmentBytes: segmentBytes, maxSegments: maxSegments))
}

private func diagnosticRecords(_ root: URL) throws -> [[String: Any]] {
    try FileManager.default.contentsOfDirectory(at: root.appendingPathComponent("spool"), includingPropertiesForKeys: nil)
        .filter { $0.pathExtension == "ndjson" }
        .flatMap { try Data(contentsOf: $0).split(separator: 10).map { try JSONSerialization.jsonObject(with: Data($0)) as! [String: Any] } }
}

private final class WarningCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var value = 0
    func observe() { lock.lock(); value += 1; lock.unlock() }
    var count: Int { lock.lock(); defer { lock.unlock() }; return value }
}

@Test func diagnosticsIdentityAndNativePlatformAreExplicit() {
    #expect(QuizzlerDiagnostics.identity(bundleID: nil, version: "1.0", build: "1") == nil)
    #expect(QuizzlerDiagnostics.identity(bundleID: "other.invalid", version: "1.0", build: "1") == nil)
    #expect(QuizzlerDiagnostics.identity(bundleID: "com.zerodelta.quizzler", version: "bad", build: "1") == nil)
    let identity = QuizzlerDiagnostics.identity(bundleID: "com.zerodelta.quizzler", version: "1.0", build: "1")
    #expect(identity?.project == "quizzler")
    #if targetEnvironment(macCatalyst) || os(macOS)
    #expect(identity?.platform == .macos)
    #expect(QuizzlerDiagnostics.protectionPolicy == "privatePOSIX")
    #else
    #expect(identity?.platform == .ios)
    #expect(QuizzlerDiagnostics.protectionPolicy == "completeUntilFirstUserAuthentication")
    #endif
}

@Test func diagnosticsRetryStateIsSafeFactWithoutAttemptOrGuessedOperation() async throws {
    let (root, configuration) = try diagnosticsFixture()
    defer { try? FileManager.default.removeItem(at: root) }
    let adapter = QuizzlerDiagnostics(configuration: configuration)
    #expect(!FileManager.default.fileExists(atPath: configuration.directory.path))
    await adapter.record(SyncStatusEvent(state: .retryScheduled, reason: .retryBackoff,
        pendingOperationCount: 2, pendingIssueCount: 1, retryAttempt: 3, retryAfterMilliseconds: 1500))
    await adapter.record(SyncStatusEvent(state: .failed, reason: .recordFailure))
    #expect(await adapter.flush() == .persisted)
    let records = try diagnosticRecords(root)
    #expect(records.count == 2)
    #expect(records.allSatisfy { $0["operation_id"] == nil && $0["install"] == nil && $0["device_alias"] == nil && $0["origin"] as? String == "app" })
    let first = records.first?["attributes"] as? [String: Any]
    #expect(first?["count"] as? Int == 3)
    #expect(first?["queue_depth"] as? Int == 3)
    #expect(first?["duration_ms"] as? Int == 1500)
    #expect(first?["retryable"] as? Bool == true)
    #expect(first?["attempt"] == nil)
    #expect(records.last?["severity"] as? String == "WARN")
    #expect(records.allSatisfy { ($0["attributes"] as? [String: Any])?["error_domain"] == nil })
}

@Test func diagnosticsLifecycleStartIsOnceAndBackgroundFlushDoesNotClose() async throws {
    let (root, configuration) = try diagnosticsFixture()
    defer { try? FileManager.default.removeItem(at: root) }
    let adapter = QuizzlerDiagnostics(configuration: configuration)
    await adapter.lifecycle(.start); await adapter.lifecycle(.start)
    await adapter.lifecycle(.background)
    #expect(await adapter.flush() == .persisted)
    #expect(await adapter.status()?.closed == false)
    await adapter.record(SyncStatusEvent(state: .offline, reason: .unreachable))
    #expect(await adapter.lastAdmission == .admitted)
    #expect(await adapter.flush() == .persisted)
    let records = try diagnosticRecords(root)
    #expect(records.count == 3)
    #expect(records.filter { ($0["attributes"] as? [String: Any])?["code"] as? String == "lifecycle-start" }.count == 1)
    #expect(records.filter { $0["kind"] as? String == "lifecycle" && ($0["attributes"] as? [String: Any])?["code"] as? String == "unknown" }.count == 1)
    #expect(records.allSatisfy { ($0["attributes"] as? [String: Any])?["code"] as? String != "lifecycle-stop" })
    #expect(await adapter.status()?.receiptTrust == .unbound)
}

@Test func diagnosticsBackgroundFlushKeepsDefaultSpoolSegmentUnsealed() async throws {
    guard let resolved = realpath(NSTemporaryDirectory(), nil) else { throw DiagnosticsError.io }
    defer { free(resolved) }
    let root = URL(fileURLWithPath: String(cString: resolved)).appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false,
        attributes: [.posixPermissions: 0o700])
    defer { try? FileManager.default.removeItem(at: root) }
    let identity = try AppIdentity(project: "quizzler", bundleID: "com.zerodelta.quizzler",
        platform: .macos, appVersion: "1.0", build: "1")
    let configuration = try DiagnosticsConfiguration(identity: identity,
        directory: root.appendingPathComponent("spool"))
    #expect(configuration.segmentBytes == 1_048_576)
    #expect(configuration.maxSegments == 8)

    let adapter = QuizzlerDiagnostics(configuration: configuration)
    for _ in 0..<12 {
        await adapter.lifecycle(.background)
        #expect(await adapter.flush() == .persisted)
    }

    let status = await adapter.status()
    #expect(status?.runLoss.admittedCount == 12)
    #expect(status?.refusedCount == 0)
    #expect(try diagnosticRecords(root).count == 12)
    let segmentFiles = try FileManager.default.contentsOfDirectory(at: configuration.directory,
        includingPropertiesForKeys: nil).filter { $0.pathExtension == "ndjson" }
    #expect(segmentFiles.count == 1)
}

@Test func diagnosticsInvalidFactsWarnOnceAndDefaultTestsStayIsolated() async throws {
    let (root, configuration) = try diagnosticsFixture()
    defer { try? FileManager.default.removeItem(at: root) }
    let counter = WarningCounter()
    let adapter = QuizzlerDiagnostics(configuration: configuration, isolated: false, warningObserver: { counter.observe() })
    let overflow = SyncStatusEvent(state: .retryScheduled, reason: .retryBackoff, pendingOperationCount: Int.max, pendingIssueCount: 1)
    await adapter.record(overflow); await adapter.record(overflow)
    #expect(await adapter.unavailable == .invalidFact)
    #expect(counter.count == 1)
    #expect(!FileManager.default.fileExists(atPath: configuration.directory.path))
    let isolated = QuizzlerDiagnostics()
    await isolated.lifecycle(.start)
    #expect(await isolated.unavailable == .isolatedRun)
    #expect(await isolated.status() == nil)
}

@Test func diagnosticsDiskRefusalRetainsUnacknowledgedBytesWithoutChangingFacts() async throws {
    let (root, configuration) = try diagnosticsFixture(segmentBytes: 512, maxSegments: 1)
    defer { try? FileManager.default.removeItem(at: root) }
    let counter = WarningCounter()
    let adapter = QuizzlerDiagnostics(configuration: configuration, isolated: false, warningObserver: { counter.observe() })
    await adapter.lifecycle(.start)
    #expect(await adapter.flush() == .persisted)
    let before = try diagnosticRecords(root)
    await adapter.lifecycle(.background)
    #expect(await adapter.flush() == .refused)
    #expect(await adapter.status()?.uncertain == false)
    #expect((await adapter.status()?.refusedCount ?? 0) > 0)
    #expect(try diagnosticRecords(root).count == before.count)
    #expect(counter.count == 1)
}

@Test func diagnosticsStorageCreatesReusesAndRefusesAmbiguityWithoutMutation() throws {
    let (root, _) = try diagnosticsFixture()
    defer { try? FileManager.default.removeItem(at: root) }
    let parent = root.appendingPathComponent("private")
    var attributes: [FileAttributeKey: Any] = [.posixPermissions: 0o700]
    #if os(iOS) && !targetEnvironment(macCatalyst)
    attributes[.protectionKey] = FileProtectionType.completeUntilFirstUserAuthentication
    #endif
    try QuizzlerDiagnostics.prepareDirectory(parent, attributes: attributes, intermediates: false, excludeFromBackup: true)
    #expect(try parent.resourceValues(forKeys: [.isExcludedFromBackupKey]).isExcludedFromBackup == true)
    let sentinel = parent.appendingPathComponent("sentinel")
    let bytes = Data("owned-test-bytes".utf8)
    try bytes.write(to: sentinel)
    try QuizzlerDiagnostics.prepareDirectory(parent, attributes: attributes, intermediates: false, excludeFromBackup: true)
    #expect(try Data(contentsOf: sentinel) == bytes)
    let symlink = root.appendingPathComponent("parent-link")
    try FileManager.default.createSymbolicLink(at: symlink, withDestinationURL: parent)
    #expect(throws: DiagnosticsError.self) { try QuizzlerDiagnostics.prepareDirectory(symlink, attributes: attributes, intermediates: false) }
    let spool = parent.appendingPathComponent("spool")
    try FileManager.default.createSymbolicLink(at: spool, withDestinationURL: root)
    #expect(throws: DiagnosticsError.self) { try QuizzlerDiagnostics.prepareDirectory(spool, attributes: attributes, intermediates: false) }
    try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: parent.path)
    #expect(throws: DiagnosticsError.self) { try QuizzlerDiagnostics.prepareDirectory(parent, attributes: attributes, intermediates: false, excludeFromBackup: true) }
    #expect((try FileManager.default.attributesOfItem(atPath: parent.path)[.posixPermissions] as? NSNumber)?.intValue == 0o755)
    #expect(try Data(contentsOf: sentinel) == bytes)
}

@Test func diagnosticsExactFlagsIgnoreExecutableAndUnrelatedSubstrings() {
    #expect(!QuizzlerDiagnostics.hasIsolatedArguments(["/fixture/ui-test/App", "--ordinary-fixture-path"]))
    #expect(QuizzlerDiagnostics.hasIsolatedArguments(["App", "--quizzler-development-cloudkit-probe"]))
    #expect(QuizzlerDiagnostics.hasIsolatedArguments(["App", "--quizzler-development-cloudkit-probe-recover"]))
    #expect(QuizzlerDiagnostics.hasIsolatedArguments(["App", "--quizzler-ui-test-launch-sting-settled"]))
}

@Test func diagnosticsFixedMappingAndBoundsPreserveLatchedRefusalHistory() async throws {
    let (root, configuration) = try diagnosticsFixture(segmentBytes: 16_384)
    defer { try? FileManager.default.removeItem(at: root) }
    let counter = WarningCounter()
    let adapter = QuizzlerDiagnostics(configuration: configuration, isolated: false, warningObserver: { counter.observe() })
    var seen = Set<String>()
    for state in [SyncStatusState.retryScheduled, .offline, .accountIsolationRequired, .recoveryRequired] {
        await adapter.record(.init(state: state, reason: .recordFailure))
        #expect(await adapter.flush() == .persisted)
        let records = try diagnosticRecords(root)
        let fresh = records.filter { !seen.contains($0["event_id"] as! String) }
        #expect(fresh.count == 1)
        #expect(fresh.first?["severity"] as? String == ([.retryScheduled, .offline].contains(state) ? "INFO" : "WARN"))
        seen = Set(records.map { $0["event_id"] as! String })
    }
    await adapter.record(.init(state: .synced, reason: .statePersistenceFailed))
    #expect(await adapter.flush() == .persisted)
    for fact in [SyncStatusEvent(state: .failed, reason: .recordFailure, pendingOperationCount: 1_000_001),
                 SyncStatusEvent(state: .failed, reason: .recordFailure, retryAttempt: 1_000_000_001),
                 SyncStatusEvent(state: .failed, reason: .recordFailure, retryAfterMilliseconds: 86_400_001)] {
        await adapter.record(fact)
    }
    #expect(counter.count == 1)
    #expect(await adapter.unavailable == .invalidFact)
    await adapter.record(.init(state: .offline, reason: .unreachable, pendingOperationCount: -1, retryAttempt: -1))
    #expect(await adapter.flush() == .persisted)
    #expect(await adapter.unavailable == .invalidFact)
    let records = try diagnosticRecords(root)
    #expect(records.count == 6)
    #expect(records.filter { $0["severity"] as? String == "WARN" }.count == 3)
    let storage = records.filter { ($0["attributes"] as? [String: Any])?["code"] as? String == "storage-failure" }
    #expect(storage.count == 1)
    #expect(storage.first?["severity"] as? String == "WARN")
    #expect(records.allSatisfy { ($0["attributes"] as? [String: Any])?["queue_depth"] as? Int == 0 })
}

@Test func diagnosticsSharedSettledFixturePredicateCoversAllAliases() {
    for flag in ["--quizzler-ui-test-launch-sting-settled", "--quizzler-launch-sting-settled", "--launch-sting-settled"] {
        #expect(QuizzlerDiagnostics.isSettledFixtureLaunch(arguments: ["App", flag], environment: [:]))
        #expect(QuizzlerDiagnostics.hasIsolatedArguments(["App", flag]))
        #expect(!QuizzlerDiagnostics.isSettledFixtureLaunch(arguments: [flag], environment: [:]))
    }
    for key in ["QUIZZLER_UI_TEST_LAUNCH_STING_SETTLED", "QUIZZLER_LAUNCH_STING_SETTLED"] {
        #expect(QuizzlerDiagnostics.isSettledFixtureLaunch(arguments: ["App"], environment: [key: "enabled"]))
        #expect(!QuizzlerDiagnostics.isSettledFixtureLaunch(arguments: ["App"], environment: [key: "disabled"]))
    }
    #expect(!QuizzlerDiagnostics.isSettledFixtureLaunch(arguments: ["/fixture/ui-test/App", "--ordinary-fixture"], environment: [:]))
}
