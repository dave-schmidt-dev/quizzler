import Foundation
import XCTest
@testable import QuizzlerKit

final class PackDecodingTests: XCTestCase {
    func testInstallablePackDecodesAndRejectsDuplicateIDs() throws {
        let data = try fixture(type: "multiple_choice", id: "q1")
        let manifest = try PackLoader().load(data: data)
        XCTAssertEqual(manifest.questions.count, 1)
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let question = try XCTUnwrap((object["questions"] as? [[String: Any]])?.first)
        object["questions"] = [question, question]
        XCTAssertThrowsError(try PackLoader().load(data: JSONSerialization.data(withJSONObject: object)))
    }

    func testUnknownTypeAndInvalidAnswerFailClosed() throws {
        XCTAssertThrowsError(try PackLoader().load(data: fixture(type: "ordering", id: "q1")))
        XCTAssertThrowsError(try PackLoader().load(data: fixture(type: "multiple_choice", id: "q1", answer: 9)))
    }

    func testLegacyTypeNeedsExactDigestAllowlist() throws {
        let data = try fixture(type: "true_false", id: "q1", trueFalse: true)
        XCTAssertThrowsError(try PackLoader().load(data: data))
        let digest = PackLoader.contentDigest(for: data)
        XCTAssertNoThrow(try PackLoader(legacyDigestAllowlist: [digest]).load(data: data))
        XCTAssertThrowsError(try PackLoader(legacyDigestAllowlist: ["*"]).load(data: data))
    }

    func testAssetManifestDecodesDeterministicallyAndRejectsUnknownOrMissingKeys() throws {
        let assetA: [String: Any] = [
            "course_id": "course-a",
            "pack_id": "pack-a",
            "path": "course-a/pack.json",
            "content_digest": "sha256:" + String(repeating: "a", count: 64)
        ]
        let assetB: [String: Any] = [
            "course_id": "course-b",
            "pack_id": "pack-b",
            "path": "course-b/pack.json",
            "content_digest": "sha256:" + String(repeating: "b", count: 64)
        ]
        let manifest: [String: Any] = ["contract_version": 1, "packs": [assetB, assetA]]
        let decoder = JSONDecoder()
        let decode: ([String: Any]) throws -> NativePackAssetManifest = {
            try decoder.decode(NativePackAssetManifest.self, from: JSONSerialization.data(withJSONObject: $0))
        }

        let decoded = try decode(manifest)
        XCTAssertEqual(decoded.packs.map(\.path), ["course-a/pack.json", "course-b/pack.json"])

        var unknownManifestKey = manifest
        unknownManifestKey["unexpected"] = true
        XCTAssertThrowsError(try decode(unknownManifestKey))

        var missingManifestKey = manifest
        missingManifestKey.removeValue(forKey: "packs")
        XCTAssertThrowsError(try decode(missingManifestKey))

        var unknownAssetKey = manifest
        unknownAssetKey["packs"] = [[
            "course_id": "course-a",
            "pack_id": "pack-a",
            "path": "course-a/pack.json",
            "content_digest": "sha256:" + String(repeating: "a", count: 64),
            "unexpected": true
        ]]
        XCTAssertThrowsError(try decode(unknownAssetKey))

        var missingAssetKey = manifest
        missingAssetKey["packs"] = [[
            "course_id": "course-a",
            "pack_id": "pack-a",
            "path": "course-a/pack.json"
        ]]
        XCTAssertThrowsError(try decode(missingAssetKey))
    }

    func testDirectAssetConstructionValidatesFieldsAndManifestOrdering() throws {
        let digestA = "sha256:" + String(repeating: "a", count: 64)
        let digestB = "sha256:" + String(repeating: "b", count: 64)
        let assetA = try NativePackAsset(courseID: "course-a", packID: "pack-a", path: "course-a/pack.json", contentDigest: digestA)
        let assetB = try NativePackAsset(courseID: "course-b", packID: "pack-b", path: "course-b/pack.json", contentDigest: digestB)

        let manifest = try NativePackAssetManifest(packs: [assetB, assetA])
        XCTAssertEqual(manifest.packs.map(\.path), ["course-a/pack.json", "course-b/pack.json"])

        let duplicateID = try NativePackAsset(courseID: "course-a", packID: "pack-a", path: "course-a/other.json", contentDigest: digestA)
        XCTAssertThrowsError(try NativePackAssetManifest(packs: [assetA, duplicateID]))

        let duplicatePath = try NativePackAsset(courseID: "course-c", packID: "pack-c", path: assetA.path, contentDigest: digestA)
        XCTAssertThrowsError(try NativePackAssetManifest(packs: [assetA, duplicatePath]))

        let invalidAssets = [
            (courseID: " ", packID: "pack", path: "course/pack.json", digest: digestA),
            (courseID: "course", packID: "\n", path: "course/pack.json", digest: digestA),
            (courseID: "course", packID: "pack", path: " ", digest: digestA),
            (courseID: "course", packID: "pack", path: "/course/pack.json", digest: digestA),
            (courseID: "course", packID: "pack", path: "course/../pack.json", digest: digestA),
            (courseID: "course", packID: "pack", path: "course/pack.json", digest: "sha256:not-a-digest")
        ]
        for invalid in invalidAssets {
            XCTAssertThrowsError(try NativePackAsset(courseID: invalid.courseID, packID: invalid.packID, path: invalid.path, contentDigest: invalid.digest))
        }
    }

    func testQuestionAssetManifestRoundTripsAndRejectsInvalidDecodedAssets() throws {
        let validDigest = "sha256:" + String(repeating: "a", count: 64)
        let asset = try NativePackAsset(courseID: "course-a", packID: "pack-a", path: "course-a/pack.json", contentDigest: validDigest)
        let manifest = try QuestionAssetManifest(packs: [asset])
        let decoder = JSONDecoder()

        let encoded = try JSONEncoder().encode(manifest)
        let decoded = try decoder.decode(QuestionAssetManifest.self, from: encoded)
        XCTAssertEqual(decoded, manifest)
        let encodedObject = try XCTUnwrap(JSONSerialization.jsonObject(with: encoded) as? [String: Any])
        XCTAssertEqual(Set(encodedObject.keys), ["contract_version", "packs"])

        let decode: ([String: Any]) throws -> QuestionAssetManifest = {
            try decoder.decode(QuestionAssetManifest.self, from: JSONSerialization.data(withJSONObject: $0))
        }
        for invalidPath in ["/course-a/pack.json", "course-a/../pack.json"] {
            var invalidManifest: [String: Any] = ["contract_version": 1]
            invalidManifest["packs"] = [[
                "course_id": "course-a",
                "pack_id": "pack-a",
                "path": invalidPath,
                "content_digest": validDigest
            ]]
            XCTAssertThrowsError(try decode(invalidManifest))
        }

        var invalidDigestManifest: [String: Any] = ["contract_version": 1]
        invalidDigestManifest["packs"] = [[
            "course_id": "course-a",
            "pack_id": "pack-a",
            "path": "course-a/pack.json",
            "content_digest": "sha256:not-a-digest"
        ]]
        XCTAssertThrowsError(try decode(invalidDigestManifest))
    }

    private func fixture(type: String, id: String, answer: Int = 0, trueFalse: Bool? = nil) throws -> Data {
        var q: [String: Any] = ["id": id, "type": type, "topic": "topic", "exam_area": "area", "difficulty": "easy", "prompt": "Prompt", "explanation": "Explanation"]
        if let trueFalse { q["answer"] = trueFalse } else { q["options"] = ["A", "B"]; q["answer"] = answer }
        return try JSONSerialization.data(withJSONObject: ["pack_id": "p", "subject": "s", "title": "t", "version": 1, "questions": [q]])
    }
}
