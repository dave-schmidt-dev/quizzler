import Foundation

/// One pack that was bundled into the app and successfully decoded.
public struct InstalledPack: Identifiable, Equatable, Sendable {
    public let courseID: String
    public let manifest: PackManifest

    public init(courseID: String, manifest: PackManifest) {
        self.courseID = courseID
        self.manifest = manifest
    }

    public var id: String { "\(courseID)/\(manifest.packID)" }
    public var packID: String { manifest.packID }
    /// The course label a screen shows — the pack's own `subject`, never a
    /// constant compiled into the app.
    public var subject: String { manifest.subject }
    public var questions: [Question] { manifest.questions }

    public func identity(for question: Question) -> QuestionIdentity {
        QuestionIdentity(courseID: courseID, packID: manifest.packID, questionID: question.id)
    }
}

/// A pack that was listed in the asset manifest but could not be used.
///
/// These are surfaced rather than swallowed: a course going missing from the
/// app with no visible reason is the exact failure this type exists to make
/// impossible.
public struct PackLoadFailure: Equatable, Sendable {
    public let path: String
    public let reason: String

    public init(path: String, reason: String) {
        self.path = path
        self.reason = reason
    }
}

public enum PackCatalogError: Error, Equatable, Sendable {
    case manifestMissing
    case manifestUnreadable
}

/// The set of question packs installed in an app bundle.
///
/// Content is discovered at build time (`scripts/build_pack_assets.py`) and
/// verified here: every pack is checked against the digest recorded in the
/// manifest before its questions are used, so a pack cannot be swapped after
/// the digest was taken.
public struct PackCatalog: Sendable {
    /// Written by the build phase into the app's resource directory.
    public static let manifestResourceName = "question-assets"
    public static let manifestResourceExtension = "json"
    /// Pack files keep their `<course>/<file>.json` layout under this folder.
    public static let packsSubdirectory = "Packs"

    public let packs: [InstalledPack]
    public let failures: [PackLoadFailure]

    public init(packs: [InstalledPack] = [], failures: [PackLoadFailure] = []) {
        self.packs = packs
        self.failures = failures
    }

    public var isEmpty: Bool { packs.isEmpty }

    /// The pack a fresh install studies.
    ///
    /// Course selection is deliberately not a user choice yet: the app takes
    /// the first real course in manifest order and falls back to `samples`
    /// only when nothing else is installed, so a clean checkout still runs
    /// while a machine with real material studies that material.
    public var primaryPack: InstalledPack? {
        packs.first { $0.courseID != "samples" } ?? packs.first
    }

    public static func load(bundle: Bundle = .main) throws -> PackCatalog {
        guard let manifestURL = bundle.url(forResource: manifestResourceName, withExtension: manifestResourceExtension) else {
            throw PackCatalogError.manifestMissing
        }
        // Pack paths in the manifest are relative to the resource root, which
        // is also where the manifest itself lives.
        let root = manifestURL.deletingLastPathComponent()
        return try load(manifestURL: manifestURL, packsRoot: root.appendingPathComponent(packsSubdirectory, isDirectory: true))
    }

    public static func load(manifestURL: URL, packsRoot: URL) throws -> PackCatalog {
        guard let data = try? Data(contentsOf: manifestURL) else { throw PackCatalogError.manifestMissing }
        guard let manifest = try? JSONDecoder().decode(QuestionAssetManifest.self, from: data) else {
            throw PackCatalogError.manifestUnreadable
        }

        let loader = PackLoader()
        var packs: [InstalledPack] = []
        var failures: [PackLoadFailure] = []

        for asset in manifest.assets {
            let url = packsRoot.appendingPathComponent(asset.path, isDirectory: false)
            do {
                // `expectedDigest` is what makes the manifest meaningful: the
                // pack is rejected if its bytes no longer hash to the value
                // recorded when it was bundled.
                let pack = try loader.load(url: url, expectedDigest: asset.contentDigest)
                guard pack.packID == asset.packID else {
                    failures.append(PackLoadFailure(path: asset.path, reason: "declares pack_id \(pack.packID), manifest says \(asset.packID)"))
                    continue
                }
                packs.append(InstalledPack(courseID: asset.courseID, manifest: pack))
            } catch {
                failures.append(PackLoadFailure(path: asset.path, reason: describe(error)))
            }
        }

        return PackCatalog(packs: packs, failures: failures)
    }

    private static func describe(_ error: Error) -> String {
        switch error {
        case PackLoaderError.digestMismatch(let expected, let actual):
            "content digest \(actual) does not match the bundled \(expected)"
        case PackLoaderError.invalidJSON:
            "file is not readable JSON"
        case PackLoaderError.invalidManifest:
            "fails the pack contract (see PackManifest.validate)"
        case PackLoaderError.legacyDigestNotAllowlisted:
            "contains question types no longer installable"
        default:
            String(describing: error)
        }
    }
}
