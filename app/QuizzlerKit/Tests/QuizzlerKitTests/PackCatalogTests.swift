import Foundation
import XCTest
@testable import QuizzlerKit

/// Canonical-JSON agreement between the Swift decoder and the Python authoring
/// pipeline.
///
/// The digest recorded in `question-assets.json` is computed in Python by
/// `scripts/build_pack_assets.py` and verified here at load time, so the two
/// languages must serialize identically before hashing. They previously did
/// not: Foundation writes `/` as `\/` unless told otherwise, which made every
/// pack containing a URL hash differently on the two sides.
///
/// `tests/test_build_pack_assets.py` parses these same vectors out of this
/// file and asserts Python reaches the same digests. Restating the values in
/// both languages would let them drift while both suites still passed, so the
/// Python side reads this array rather than keeping a copy.
enum PackDigestVector {
    /// (label, JSON text, expected `sha256:` digest)
    static let all: [(label: String, json: String, digest: String)] = [
        ("plain", #"{"a":1,"b":"two"}"#, "sha256:f15bfc93d70801047473922f67fed863ecc7f82f0677ebb7122923aee81e0f97"),
        ("slash", #"{"source":"Official (ISC)2/Sybex"}"#, "sha256:34e8e37c7f2af19442c88fd45f532fa2ac7824dffd055a590b4a2f0bbf61a28d"),
        ("unicode", #"{"prompt":"café — naïve · 日本語"}"#, "sha256:a486574069c7542dd510eac51846dcfa1247fd46d4fba1b093bc614efd32c7c2"),
        ("escapes", #"{"text":"line\nbreak\ttab \"quoted\" back\\slash"}"#, "sha256:7606221377cecf321a11d896f943f499bae3c0678d3d9b9aba1fdfb5a3bc39e7"),
        ("nested", #"{"z":[1,2,{"y":null,"x":true}],"a":{"c":3,"b":[]}}"#, "sha256:7d3fe8477c0d42bd9182b6543f3f1e878a7c2f65f37ed194bed52624fb64f85e")
    ]
}

final class PackDigestVectorTests: XCTestCase {
    func testEveryVectorHashesToItsRecordedDigest() throws {
        for vector in PackDigestVector.all {
            let data = Data(vector.json.utf8)
            XCTAssertEqual(PackLoader.contentDigest(for: data), vector.digest, "vector \(vector.label)")
        }
    }

    func testDigestIgnoresKeyOrderAndWhitespace() throws {
        let compact = Data(#"{"a":1,"b":[2,3]}"#.utf8)
        let reordered = Data("{\n  \"b\" : [2, 3],\n  \"a\" : 1\n}".utf8)
        XCTAssertEqual(PackLoader.contentDigest(for: compact), PackLoader.contentDigest(for: reordered))
    }
}

final class PackCatalogTests: XCTestCase {
    private var root: URL!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent("pack-catalog-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: root)
    }

    func testLoadsEveryListedPackAndExposesItsSubject() throws {
        let pack = try writePack(course: "cissp", file: "core.json", packID: "cissp-core", subject: "CISSP")
        try writeManifest([pack])

        let catalog = try PackCatalog.load(manifestURL: manifestURL, packsRoot: packsRoot)

        XCTAssertEqual(catalog.packs.count, 1)
        XCTAssertTrue(catalog.failures.isEmpty)
        XCTAssertFalse(catalog.isEmpty)
        XCTAssertEqual(catalog.primaryPack?.subject, "CISSP")
        XCTAssertEqual(catalog.primaryPack?.courseID, "cissp")
        XCTAssertEqual(catalog.primaryPack?.questions.count, 1)
    }

    func testPrimaryPackPrefersARealCourseOverSamples() throws {
        let samples = try writePack(course: "samples", file: "demo.json", packID: "samples-demo", subject: "Samples")
        let cissp = try writePack(course: "cissp", file: "core.json", packID: "cissp-core", subject: "CISSP")
        try writeManifest([samples, cissp])

        let catalog = try PackCatalog.load(manifestURL: manifestURL, packsRoot: packsRoot)

        XCTAssertEqual(catalog.packs.count, 2)
        XCTAssertEqual(catalog.primaryPack?.courseID, "cissp")
    }

    func testSamplesIsUsedWhenItIsTheOnlyInstalledCourse() throws {
        let samples = try writePack(course: "samples", file: "demo.json", packID: "samples-demo", subject: "Samples")
        try writeManifest([samples])

        let catalog = try PackCatalog.load(manifestURL: manifestURL, packsRoot: packsRoot)

        XCTAssertEqual(catalog.primaryPack?.courseID, "samples")
    }

    func testATamperedPackIsRefusedAndReportedRatherThanUsed() throws {
        let pack = try writePack(course: "cissp", file: "core.json", packID: "cissp-core", subject: "CISSP")
        try writeManifest([pack])
        // Rewrite the file after its digest was recorded.
        let target = packsRoot.appendingPathComponent("cissp/core.json")
        try Data(#"{"pack_id":"cissp-core","subject":"CISSP","title":"t","version":1,"questions":[{"id":"q2","type":"multiple_choice","topic":"topic","exam_area":"area","difficulty":"easy","prompt":"Swapped","explanation":"Swapped","options":["A","B"],"answer":0}]}"#.utf8).write(to: target)

        let catalog = try PackCatalog.load(manifestURL: manifestURL, packsRoot: packsRoot)

        XCTAssertTrue(catalog.isEmpty)
        XCTAssertEqual(catalog.failures.count, 1)
        XCTAssertEqual(catalog.failures.first?.path, "cissp/core.json")
        XCTAssertTrue(catalog.failures.first?.reason.contains("digest") == true)
    }

    func testAMissingPackFileIsReportedWithoutLosingTheOthers() throws {
        let present = try writePack(course: "cissp", file: "core.json", packID: "cissp-core", subject: "CISSP")
        let absent = NativePackAssetStub(courseID: "gone", packID: "gone-pack", path: "gone/missing.json", contentDigest: "sha256:" + String(repeating: "a", count: 64))
        try writeManifest([present, absent])

        let catalog = try PackCatalog.load(manifestURL: manifestURL, packsRoot: packsRoot)

        XCTAssertEqual(catalog.packs.count, 1)
        XCTAssertEqual(catalog.failures.map(\.path), ["gone/missing.json"])
    }

    func testAPackWhoseIDDisagreesWithTheManifestIsRefused() throws {
        var pack = try writePack(course: "cissp", file: "core.json", packID: "cissp-core", subject: "CISSP")
        pack.packID = "something-else"
        try writeManifest([pack])

        let catalog = try PackCatalog.load(manifestURL: manifestURL, packsRoot: packsRoot)

        XCTAssertTrue(catalog.isEmpty)
        XCTAssertTrue(catalog.failures.first?.reason.contains("manifest says") == true)
    }

    func testAnEmptyManifestLoadsAsAnEmptyCatalogRatherThanThrowing() throws {
        try writeManifest([])
        let catalog = try PackCatalog.load(manifestURL: manifestURL, packsRoot: packsRoot)
        XCTAssertTrue(catalog.isEmpty)
        XCTAssertNil(catalog.primaryPack)
        XCTAssertTrue(catalog.failures.isEmpty)
    }

    func testAMissingOrUnreadableManifestThrows() throws {
        XCTAssertThrowsError(try PackCatalog.load(manifestURL: manifestURL, packsRoot: packsRoot)) {
            XCTAssertEqual($0 as? PackCatalogError, .manifestMissing)
        }
        try Data("not json".utf8).write(to: manifestURL)
        XCTAssertThrowsError(try PackCatalog.load(manifestURL: manifestURL, packsRoot: packsRoot)) {
            XCTAssertEqual($0 as? PackCatalogError, .manifestUnreadable)
        }
    }

    func testIdentityCarriesTheCourseFromTheManifestNotThePack() throws {
        let pack = try writePack(course: "cissp", file: "core.json", packID: "cissp-core", subject: "CISSP")
        try writeManifest([pack])
        let catalog = try PackCatalog.load(manifestURL: manifestURL, packsRoot: packsRoot)
        let installed = try XCTUnwrap(catalog.primaryPack)
        let question = try XCTUnwrap(installed.questions.first)
        XCTAssertEqual(installed.identity(for: question), QuestionIdentity(courseID: "cissp", packID: "cissp-core", questionID: question.id))
    }

    // MARK: - Fixtures

    private struct NativePackAssetStub {
        let courseID: String
        var packID: String
        let path: String
        let contentDigest: String

        var json: [String: Any] {
            ["course_id": courseID, "pack_id": packID, "path": path, "content_digest": contentDigest]
        }
    }

    private var manifestURL: URL { root.appendingPathComponent("question-assets.json") }
    private var packsRoot: URL { root.appendingPathComponent("Packs", isDirectory: true) }

    private func writePack(course: String, file: String, packID: String, subject: String) throws -> NativePackAssetStub {
        let body: [String: Any] = [
            "pack_id": packID,
            "subject": subject,
            "title": "Core",
            "version": 1,
            "questions": [[
                "id": "q1", "type": "multiple_choice", "topic": "topic", "exam_area": "area",
                "difficulty": "easy", "prompt": "Prompt", "explanation": "Explanation",
                "options": ["A", "B"], "answer": 0
            ]]
        ]
        let data = try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys, .withoutEscapingSlashes])
        let target = packsRoot.appendingPathComponent(course, isDirectory: true).appendingPathComponent(file)
        try FileManager.default.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: target)
        return NativePackAssetStub(courseID: course, packID: packID, path: "\(course)/\(file)", contentDigest: PackLoader.contentDigest(for: data))
    }

    private func writeManifest(_ assets: [NativePackAssetStub]) throws {
        let body: [String: Any] = ["contract_version": 1, "packs": assets.map(\.json)]
        try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys]).write(to: manifestURL)
    }
}
