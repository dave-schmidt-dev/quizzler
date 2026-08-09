import Foundation

public enum PackLoaderError: Error, Equatable, Sendable {
    case invalidLegacyAllowlist
    case digestMismatch(expected: String, actual: String)
    case legacyDigestNotAllowlisted(String)
    case invalidJSON
    case invalidManifest
}

/// Decodes local immutable packs. No loader API accepts a CloudKit record or
/// writes pack content to a sync payload.
public struct PackLoader: Sendable {
    public let legacyDigestAllowlist: Set<String>

    public init(legacyDigestAllowlist: Set<String> = []) {
        self.legacyDigestAllowlist = legacyDigestAllowlist
    }

    public func load(data: Data, expectedDigest: String? = nil) throws -> PackManifest {
        let digest = Self.contentDigest(for: data)
        if let expectedDigest, expectedDigest != digest { throw PackLoaderError.digestMismatch(expected: expectedDigest, actual: digest) }
        guard legacyDigestAllowlist.allSatisfy(Self.isDigest) else { throw PackLoaderError.invalidLegacyAllowlist }
        let decoder = JSONDecoder()
        decoder.userInfo[.allowLegacyQuestionTypes] = legacyDigestAllowlist.contains(digest)
        do {
            let manifest = try decoder.decode(PackManifest.self, from: data)
            if manifest.questions.contains(where: { !$0.type.isInstallable }) && !legacyDigestAllowlist.contains(digest) {
                throw PackLoaderError.legacyDigestNotAllowlisted(digest)
            }
            return manifest
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
    public static func contentDigest(for data: Data) -> String {
        let canonical: Data
        if let object = try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed]), JSONSerialization.isValidJSONObject(object), let encoded = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) {
            canonical = encoded
        } else { canonical = data }
        return "sha256:" + SHA256.hash(canonical).map { String(format: "%02x", $0) }.joined()
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
    public init(courseID: String, packID: String, path: String, contentDigest: String) { self.courseID = courseID; self.packID = packID; self.path = path; self.contentDigest = contentDigest }
    enum CodingKeys: String, CodingKey { case courseID = "course_id", packID = "pack_id", path, contentDigest = "content_digest" }
}

public struct NativePackAssetManifest: Codable, Equatable, Sendable {
    public static let contractVersion = 1
    public let contractVersion: Int
    public let packs: [NativePackAsset]
    public init(packs: [NativePackAsset]) { self.contractVersion = Self.contractVersion; self.packs = packs.sorted { ($0.courseID, $0.path) < ($1.courseID, $1.path) } }
}

private enum SHA256 {
    private static let constants: [UInt32] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
        0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
        0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
        0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
        0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
        0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
        0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
    ]

    static func hash(_ data: Data) -> [UInt8] {
        var bytes = Array(data); let bitLength = UInt64(bytes.count) * 8; bytes.append(0x80)
        while bytes.count % 64 != 56 { bytes.append(0) }
        bytes += withUnsafeBytes(of: bitLength.bigEndian, Array.init)
        var h: [UInt32] = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]
        for blockStart in stride(from: 0, to: bytes.count, by: 64) {
            var w = Array(repeating: UInt32(0), count: 64)
            for i in 0..<16 { let j = blockStart + i * 4; w[i] = UInt32(bytes[j]) << 24 | UInt32(bytes[j + 1]) << 16 | UInt32(bytes[j + 2]) << 8 | UInt32(bytes[j + 3]) }
            for i in 16..<64 { let s0 = w[i - 15].rotatedRight(7) ^ w[i - 15].rotatedRight(18) ^ (w[i - 15] >> 3); let s1 = w[i - 2].rotatedRight(17) ^ w[i - 2].rotatedRight(19) ^ (w[i - 2] >> 10); w[i] = w[i - 16] &+ s0 &+ w[i - 7] &+ s1 }
            var a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], x = h[7]
            for i in 0..<64 { let s1 = e.rotatedRight(6) ^ e.rotatedRight(11) ^ e.rotatedRight(25); let ch = (e & f) ^ (~e & g); let t1 = x &+ s1 &+ ch &+ constants[i] &+ w[i]; let s0 = a.rotatedRight(2) ^ a.rotatedRight(13) ^ a.rotatedRight(22); let maj = (a & b) ^ (a & c) ^ (b & c); let t2 = s0 &+ maj; x = g; g = f; f = e; e = d &+ t1; d = c; c = b; b = a; a = t1 &+ t2 }
            h[0] &+= a; h[1] &+= b; h[2] &+= c; h[3] &+= d; h[4] &+= e; h[5] &+= f; h[6] &+= g; h[7] &+= x
        }
        return h.flatMap { withUnsafeBytes(of: $0.bigEndian, Array.init) }
    }
}

private extension UInt32 { func rotatedRight(_ n: UInt32) -> UInt32 { (self >> n) | (self << (32 - n)) } }
