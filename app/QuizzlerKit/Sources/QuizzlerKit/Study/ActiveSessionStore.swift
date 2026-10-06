import Foundation

/// Device-local persistence for in-progress sessions, keyed by pack.
///
/// One file holds every pack's session as `[String: PersistedSession]` keyed by
/// `packKey`. Writes are atomic and never throw to callers. This store is
/// deliberately separate from the CloudKit-backed progress contract: resume
/// state is per-device and restoring it never records progress.
public final class ActiveSessionStore: @unchecked Sendable {
    private let fileURL: URL
    private let lock = NSLock()

    public init(fileURL: URL) {
        self.fileURL = fileURL
    }

    /// The production location, mirroring how `LocalProgressStore` picks its
    /// directory. Tests pass an explicit temporary URL.
    public static var defaultFileURL: URL {
        guard let applicationSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else {
            preconditionFailure("Application Support is unavailable")
        }
        return applicationSupport
            .appendingPathComponent("Quizzler", isDirectory: true)
            .appendingPathComponent("active-session-v1.json", isDirectory: false)
    }

    /// Returns the persisted session for one pack, or nil when none is saved.
    /// A missing file gives nil; a corrupt file is deleted and gives nil.
    public func load(packKey: String) -> PersistedSession? {
        lock.withLock {
            readSessions()[packKey]
        }
    }

    /// Persists one pack's session, replacing any previous session for that
    /// pack. A failed write leaves the previous file untouched.
    public func save(_ session: PersistedSession) {
        lock.withLock {
            var sessions = readSessions()
            sessions[session.packKey] = session
            write(sessions)
        }
    }

    /// Removes one pack's persisted session, if present.
    public func clear(packKey: String) {
        lock.withLock {
            var sessions = readSessions()
            guard sessions.removeValue(forKey: packKey) != nil else { return }
            write(sessions)
        }
    }

    /// Reads the file. A missing file is an empty store; a corrupt file is
    /// deleted and treated as empty. Never throws.
    private func readSessions() -> [String: PersistedSession] {
        guard FileManager.default.fileExists(atPath: fileURL.path) else { return [:] }
        let data: Data
        do {
            data = try Data(contentsOf: fileURL)
        } catch {
            // A read can fail transiently; leave the file alone so a later
            // attempt can retry once the condition clears.
            return [:]
        }
        do {
            return try JSONDecoder().decode([String: PersistedSession].self, from: data)
        } catch {
            try? FileManager.default.removeItem(at: fileURL)
            return [:]
        }
    }

    private func write(_ sessions: [String: PersistedSession]) {
        guard let data = try? JSONEncoder().encode(sessions) else { return }
        do {
            try FileManager.default.createDirectory(at: fileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
            try data.write(to: fileURL, options: Self.writeOptions)
        } catch {
            // Persistence is best-effort by contract: a failed write leaves
            // the previous file untouched and never surfaces an error.
        }
    }

    private static var writeOptions: Data.WritingOptions {
        #if os(iOS) || os(tvOS) || os(watchOS)
        return [.atomic, .completeFileProtection]
        #else
        // NSFileProtection is unavailable for ordinary macOS files. Keep
        // atomic replacement for package-hosted tests and macOS tooling.
        return [.atomic]
        #endif
    }
}
