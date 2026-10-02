import XCTest
@testable import QuizzleriOS
import QuizzlerKit

@MainActor
final class CurriculumCaseTests: XCTestCase {
    private var caseBundle: Bundle { .main }

    func testBundledCaseLoadsAsSyntheticSupplementalContent() throws {
        let value = try QuietPowerShellCaseLoader.loadBundled(bundle: caseBundle)

        XCTAssertTrue(value.synthetic)
        XCTAssertEqual(value.id, "quiet-powershell")
        XCTAssertEqual(Set(value.objectiveReferences.filter { $0.kind == "objective" }.map(\.id)), ["1.2", "1.3", "3.3", "4.2"])
        XCTAssertEqual(value.sources.count, 4)
        XCTAssertFalse(value.evidenceItems.isEmpty)
    }

    func testMalformedResourceFailsClosed() {
        XCTAssertThrowsError(try QuietPowerShellCaseLoader.load(data: Data("{ broken".utf8)))
    }

    func testResourceDigestMatchesCanonicalSwiftEncoding() throws {
        let resource = try JSONDecoder().decode(CurriculumEnvelopeProbe.self, from: caseResourceData())
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        let canonicalPayload = try encoder.encode(resource.payload)

        XCTAssertEqual(PackLoader.contentDigest(for: canonicalPayload), resource.sha256)
    }

    func testModifiedPayloadFailsDigestValidation() throws {
        let data = try caseResourceData()
        let original = String(decoding: data, as: UTF8.self)
        let modified = original.replacingOccurrences(
            of: "Investigate signals with evidence",
            with: "Investigate changed signals with evidence"
        )
        XCTAssertNotEqual(original, modified)

        XCTAssertThrowsError(try QuietPowerShellCaseLoader.load(data: Data(modified.utf8))) { error in
            XCTAssertEqual(error as? QuietPowerShellCaseLoadError, .invalid("SHA-256 digest mismatch"))
        }
    }

    func testDuplicateIDsFailEvenWithMatchingDigest() throws {
        var envelope = try XCTUnwrap(
            JSONSerialization.jsonObject(with: caseResourceData()) as? [String: Any]
        )
        var payload = try XCTUnwrap(envelope["payload"] as? [String: Any])
        var checks = try XCTUnwrap(payload["conceptChecks"] as? [[String: Any]])
        checks[1]["id"] = checks[0]["id"]
        payload["conceptChecks"] = checks

        let canonicalPayload = try JSONSerialization.data(
            withJSONObject: payload,
            options: [.sortedKeys, .withoutEscapingSlashes]
        )
        envelope["payload"] = payload
        envelope["sha256"] = PackLoader.contentDigest(for: canonicalPayload)
        let duplicateIDData = try JSONSerialization.data(withJSONObject: envelope)

        XCTAssertThrowsError(try QuietPowerShellCaseLoader.load(data: duplicateIDData)) { error in
            XCTAssertEqual(error as? QuietPowerShellCaseLoadError, .invalid("duplicate content identifier"))
        }
    }

    private func caseResourceData() throws -> Data {
        let url = try XCTUnwrap(caseBundle.url(forResource: "QuietPowerShellCase-v1", withExtension: "json"))
        return try Data(contentsOf: url)
    }
}

private struct CurriculumEnvelopeProbe: Decodable {
    let sha256: String
    let payload: QuietPowerShellCase
}
