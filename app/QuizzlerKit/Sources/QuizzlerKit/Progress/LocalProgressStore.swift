import Foundation

public enum LocalProgressStoreError: Error, Sendable, Equatable {
    case corruptState
    case encodedSizeRefused
    case unavailable
}

/// A small actor-backed durable cache. File writes are atomic and the in-memory
/// value changes only after the encoded value has been accepted by the sink.
public actor LocalProgressStore {
    private let fileURL: URL?
    private let maximumEncodedSize: Int
    private var loaded = false
    private var value: ProgressEnvelope?
    private var forcedFailure: LocalProgressStoreError?

    public init(fileURL: URL? = nil, maximumEncodedSize: Int = 1_048_576) {
        self.fileURL = fileURL
        self.maximumEncodedSize = maximumEncodedSize
    }

    public func read() throws -> ProgressEnvelope? {
        if !loaded {
            loaded = true
            guard let fileURL else { return value }
            guard FileManager.default.fileExists(atPath: fileURL.path) else { return nil }
            do {
                value = try JSONDecoder.progressDecoder.decode(ProgressEnvelope.self, from: Data(contentsOf: fileURL))
            } catch { throw LocalProgressStoreError.corruptState }
        }
        return value
    }

    public func write(_ envelope: ProgressEnvelope) throws {
        if let forcedFailure { throw forcedFailure }
        let data: Data
        do { data = try JSONEncoder.progressEncoder.encode(envelope) }
        catch { throw LocalProgressStoreError.unavailable }
        guard data.count <= maximumEncodedSize else { throw LocalProgressStoreError.encodedSizeRefused }
        if let fileURL {
            do {
                try FileManager.default.createDirectory(at: fileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
                try data.write(to: fileURL, options: [.atomic, .completeFileProtection])
            } catch { throw LocalProgressStoreError.unavailable }
        }
        value = envelope
        loaded = true
    }

    /// Test-only fault injection; useful for proving failed writes do not look applied.
    public func failNextWrite(with error: LocalProgressStoreError = .unavailable) {
        forcedFailure = error
    }

    public func clearWriteFailure() { forcedFailure = nil }
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
