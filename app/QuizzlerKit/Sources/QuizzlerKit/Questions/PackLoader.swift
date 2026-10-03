import CryptoKit
import Foundation

public enum PackLoaderError: Error, Equatable, Sendable {
    case digestMismatch(expected: String, actual: String)
    case invalidJSON
    case invalidManifest
}

/// Decodes local immutable packs. No loader API accepts a CloudKit record or
/// writes pack content to a sync payload.
public struct PackLoader: Sendable {
    public init() {}

    public func load(data: Data, expectedDigest: String? = nil) throws -> PackManifest {
        let digest = Self.contentDigest(for: data)
        if let expectedDigest, expectedDigest != digest { throw PackLoaderError.digestMismatch(expected: expectedDigest, actual: digest) }
        let decoder = JSONDecoder()
        do {
            return try decoder.decode(PackManifest.self, from: data)
        } catch let error as PackLoaderError { throw error }
        catch is DecodingError { throw PackLoaderError.invalidManifest }
        catch { throw PackLoaderError.invalidManifest }
    }

    public func load(url: URL, expectedDigest: String? = nil) throws -> PackManifest {
        do { return try load(data: Data(contentsOf: url), expectedDigest: expectedDigest) }
        catch let error as PackLoaderError { throw error }
        catch { throw PackLoaderError.invalidJSON }
    }

    /// Hashes the complete JSON value in a deterministic representation. JSON
    /// member order and insignificant whitespace never change the digest.
    ///
    /// `.withoutEscapingSlashes` matters beyond tidiness: packs are authored in
    /// Python and hashed here, and Foundation alone writes `/` as `\/`. Without
    /// it, any pack whose text contains a slash hashes differently on the two
    /// sides and no cross-language manifest can agree. `ProgressMerge` already
    /// canonicalizes this way; this makes the pack path match.
    public static func contentDigest(for data: Data) -> String {
        let canonical: Data
        if let object = try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed]), JSONSerialization.isValidJSONObject(object), let encoded = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .withoutEscapingSlashes]) {
            canonical = encoded
        } else { canonical = data }
        return "sha256:" + SHA256.hash(data: canonical).map { String(format: "%02x", $0) }.joined()
    }

    public static func isDigest(_ value: String) -> Bool {
        let hex = value.dropFirst(7)
        return value.hasPrefix("sha256:") && hex.count == 64 && hex.allSatisfy { $0.isHexDigit }
    }
}

/// A release asset index contains references and hashes only; pack bytes stay
/// in the app bundle and are never part of the CloudKit contract.
public struct NativePackAsset: Codable, Equatable, Sendable {
    public let courseID: String
    public let packID: String
    public let path: String
    public let contentDigest: String
    public init(courseID: String, packID: String, path: String, contentDigest: String) throws {
        guard Self.isNonBlank(courseID), Self.isNonBlank(packID), Self.isNonBlank(path),
              Self.isValidPath(path), PackLoader.isDigest(contentDigest) else {
            throw PackLoaderError.invalidManifest
        }
        self.courseID = courseID
        self.packID = packID
        self.path = path
        self.contentDigest = contentDigest
    }
    enum CodingKeys: String, CodingKey, CaseIterable { case courseID = "course_id", packID = "pack_id", path, contentDigest = "content_digest" }

    public init(from decoder: Decoder) throws {
        let allKeys = try decoder.container(keyedBy: AssetCodingKey.self)
        guard Set(allKeys.allKeys.map(\.stringValue)) == Set(CodingKeys.allCases.map(\.stringValue)) else {
            throw PackLoaderError.invalidManifest
        }
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.courseID = try container.decodeNonBlank(String.self, forKey: .courseID)
        self.packID = try container.decodeNonBlank(String.self, forKey: .packID)
        self.path = try container.decodeNonBlank(String.self, forKey: .path)
        self.contentDigest = try container.decodeNonBlank(String.self, forKey: .contentDigest)
        guard Self.isValidPath(path), PackLoader.isDigest(contentDigest) else { throw PackLoaderError.invalidManifest }
    }

    private static func isNonBlank(_ value: String) -> Bool {
        !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    fileprivate static func isValidPath(_ path: String) -> Bool {
        !path.hasPrefix("/") && !path.split(separator: "/").contains("..") && !path.isEmpty
    }
}

public struct NativePackAssetManifest: Codable, Equatable, Sendable {
    public static let contractVersion = 1
    public let contractVersion: Int
    public let packs: [NativePackAsset]
    public init(packs: [NativePackAsset]) throws {
        self.contractVersion = Self.contractVersion
        self.packs = try Self.validated(packs)
    }

    public init(from decoder: Decoder) throws {
        let allKeys = try decoder.container(keyedBy: AssetCodingKey.self)
        guard Set(allKeys.allKeys.map(\.stringValue)) == Set(CodingKeys.allCases.map(\.stringValue)) else { throw PackLoaderError.invalidManifest }
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let version = try container.decode(Int.self, forKey: .contractVersion)
        guard version == Self.contractVersion else { throw PackLoaderError.invalidManifest }
        self.contractVersion = version
        self.packs = try Self.validated(container.decode([NativePackAsset].self, forKey: .packs))
    }

    private static func validated(_ packs: [NativePackAsset]) throws -> [NativePackAsset] {
        let sorted = packs.sorted { ($0.courseID, $0.path) < ($1.courseID, $1.path) }
        let identities = sorted.map { "\($0.courseID)::\($0.packID)" }
        guard Set(identities).count == identities.count,
              Set(sorted.map(\.path)).count == sorted.count else { throw PackLoaderError.invalidManifest }
        return sorted
    }

    enum CodingKeys: String, CodingKey, CaseIterable { case contractVersion = "contract_version", packs }
}

private struct AssetCodingKey: CodingKey {
    let stringValue: String
    let intValue: Int?

    init?(stringValue: String) { self.stringValue = stringValue; self.intValue = nil }
    init?(intValue: Int) { self.stringValue = String(intValue); self.intValue = intValue }
}

private extension KeyedDecodingContainer {
    func decodeNonBlank<T: Decodable>(_ type: T.Type, forKey key: Key) throws -> T {
        let value = try decode(type, forKey: key)
        if let string = value as? String, string.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { throw PackLoaderError.invalidManifest }
        return value
    }
}
