import Foundation

public enum LocalProgressStoreError: Error, Sendable, Equatable {
    case corruptState
    case encodedSizeRefused
    case unavailable
}

/// A small actor-backed durable cache. File writes are atomic and the in-memory
/// value changes only after the encoded value has been accepted by the sink.
public actor LocalProgressStore {
    private let fileURL: URL
    private let maximumEncodedSize: Int
    private var loaded = false
    private var value: ProgressEnvelope?
    private var readFailure: LocalProgressStoreError?
#if DEBUG
    private var forcedFailure: LocalProgressStoreError?
#endif

    public init(fileURL: URL? = nil, maximumEncodedSize: Int = 1_048_576) {
        self.fileURL = fileURL ?? Self.defaultFileURL
        self.maximumEncodedSize = maximumEncodedSize
    }

    /// The production cache lives in the app's sandbox and is stable across
    /// launches. Tests should pass an explicit temporary URL.
    private static var defaultFileURL: URL {
        guard let applicationSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else {
            preconditionFailure("Application Support is unavailable")
        }
        return applicationSupport
            .appendingPathComponent("Quizzler", isDirectory: true)
            .appendingPathComponent("progress-v1.json", isDirectory: false)
    }

    public func read() throws -> ProgressEnvelope? {
        if let readFailure { throw readFailure }
        if !loaded {
            loaded = true
            guard FileManager.default.fileExists(atPath: fileURL.path) else { return nil }
            let data: Data
            do {
                data = try Data(contentsOf: fileURL)
            } catch {
                // A read can fail transiently (for example, while the
                // containing path is unavailable). Leave the store unloaded
                // so a later read or write can retry after the condition
                // clears. Corrupt state below remains sticky by design.
                loaded = false
                throw LocalProgressStoreError.unavailable
            }
            do {
                value = try JSONDecoder.progressDecoder.decode(ProgressEnvelope.self, from: data)
            } catch {
                readFailure = .corruptState
                throw LocalProgressStoreError.corruptState
            }
        }
        return value
    }

    public func write(_ envelope: ProgressEnvelope) throws {
        if !loaded { _ = try read() }
        if let readFailure { throw readFailure }
#if DEBUG
        if let forcedFailure { throw forcedFailure }
#endif
        let persistableEnvelope = try self.persistableEnvelope(envelope)
        let data: Data
        do { data = try JSONEncoder.progressEncoder.encode(persistableEnvelope) }
        catch { throw LocalProgressStoreError.unavailable }
        guard data.count <= maximumEncodedSize else { throw LocalProgressStoreError.encodedSizeRefused }
        do {
            _ = try JSONDecoder.progressDecoder.decode(ProgressEnvelope.self, from: data)
        } catch {
            throw LocalProgressStoreError.corruptState
        }
        do {
            try FileManager.default.createDirectory(at: fileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
            try data.write(to: fileURL, options: Self.writeOptions)
        } catch { throw LocalProgressStoreError.unavailable }
        value = persistableEnvelope
        loaded = true
    }

    /// Applies a read-modify-write while this actor remains isolated. This is
    /// the repository's atomic boundary; callers must not await between the
    /// read and write of a mutation.
    public func modify(
        _ transform: @Sendable (ProgressEnvelope?) throws -> ProgressEnvelope?
    ) throws -> ProgressEnvelope? {
        let current = try read()
        guard let next = try transform(current) else { return current }
        try write(next)
        return next
    }

#if DEBUG
    /// Test-only fault injection; useful for proving failed writes do not look applied.
    public func failNextWrite(with error: LocalProgressStoreError = .unavailable) {
        forcedFailure = error
    }

    public func clearWriteFailure() { forcedFailure = nil }
#endif

    private func persistableEnvelope(_ envelope: ProgressEnvelope) throws -> ProgressEnvelope {
        guard envelope.operations.count > ProgressEnvelope.operationRetention else { return envelope }
        let retryable = envelope.operations.filter { $0.status == .pending || $0.status == .failed }
        guard retryable.count <= ProgressEnvelope.operationRetention else {
            throw LocalProgressStoreError.encodedSizeRefused
        }

        var result = envelope
        let applied = envelope.operations.filter { $0.status == .applied }
        let appliedToKeep = max(0, ProgressEnvelope.operationRetention - retryable.count)
        let retainedAppliedIDs = Set(
            applied.sorted { $0.updatedAt > $1.updatedAt }
                .prefix(appliedToKeep)
                .map(\.id)
        )
        result.operations = envelope.operations.filter {
            $0.status != .applied || retainedAppliedIDs.contains($0.id)
        }
        return result
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

fileprivate extension JSONEncoder {
    static var progressEncoder: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .custom { date, encoder in
            var container = encoder.singleValueContainer()
            try container.encode(Self.progressDateFormatter.string(from: date))
        }
        return encoder
    }

    static let progressDateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'"
        return formatter
    }()
}

fileprivate extension JSONDecoder {
    static var progressDecoder: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let value = try decoder.singleValueContainer().decode(String.self)
            guard let date = JSONEncoder.progressDateFormatter.date(from: value) else {
                throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath, debugDescription: "invalid progress timestamp"))
            }
            return date
        }
        return decoder
    }
}
