import CryptoKit
import Foundation

/// A content fingerprint over an installed pack.
///
/// The fingerprint covers the course ID, pack ID, manifest version, and every
/// question's ID plus a canonical (`.sortedKeys`) JSON encoding of that
/// question, so any content change produces a new fingerprint. A persisted
/// in-progress session records the fingerprint of the pack it was built from
/// and is discarded on resume when the installed pack no longer matches.
public enum PackFingerprint {
    /// A stable fingerprint for one installed pack. Calling this repeatedly
    /// with the same pack always returns the same value.
    public static func make(for pack: InstalledPack) -> String {
        make(
            courseID: pack.courseID,
            packID: pack.packID,
            version: pack.manifest.version,
            questions: pack.questions
        )
    }

    /// Component form of `make(for:)`. `PackManifest` pins its contract version,
    /// so callers that need to exercise version sensitivity (tests) use this
    /// entry point with explicit components.
    static func make(courseID: String, packID: String, version: Int, questions: [Question]) -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        var hasher = SHA256()
        // NUL separators keep adjacent fields from blurring into each other
        // (course "a" + pack "bc" must not collide with "ab" + "c").
        hasher.update(data: Data(courseID.utf8))
        hasher.update(data: Data([0]))
        hasher.update(data: Data(packID.utf8))
        hasher.update(data: Data([0]))
        hasher.update(data: Data(String(version).utf8))
        hasher.update(data: Data([0]))
        for question in questions {
            hasher.update(data: Data(question.id.utf8))
            hasher.update(data: Data([0]))
            if let encoded = try? encoder.encode(question) {
                hasher.update(data: encoded)
            }
            hasher.update(data: Data([0]))
        }
        return "sha256:" + hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }
}
