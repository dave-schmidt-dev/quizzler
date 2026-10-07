import Foundation
import XCTest
@testable import QuizzlerKit

/// The `partial_install` marker (M4): a pack reduced by
/// `scripts/pack_quarantine.py` ships with the counts and ids of what was
/// removed, and the app labels the pack from the marker alone. These tests
/// mirror `tests/test_lint_partial_install.py`, which checks the Python half
/// of the same contract (lint L29 via `marker_shape_reasons`).
final class PartialInstallTests: XCTestCase {
    private static let recordDigest = "sha256:" + String(repeating: "a", count: 64)

    // MARK: - Fixtures (mirror the PackCatalogTests/PackDecodingTests helpers)

    private func questionObject(id: String) -> [String: Any] {
        [
            "id": id, "type": "multiple_choice", "topic": "topic", "exam_area": "area",
            "difficulty": "easy", "prompt": "Prompt \(id)", "explanation": "Explanation",
            "options": ["A", "B"], "answer": 0
        ]
    }

    /// A pack whose installed questions are `questionIDs`; `marker` is added
    /// verbatim when non-nil so malformed markers reach the decoder untouched.
    private func packObject(questionIDs: [String], marker: [String: Any]? = nil) -> [String: Any] {
        var object: [String: Any] = [
            "pack_id": "partial-core",
            "subject": "CISSP",
            "title": "Core",
            "version": 1,
            "questions": questionIDs.map(questionObject)
        ]
        if let marker { object["partial_install"] = marker }
        return object
    }

    /// The marker for two installed questions (`q1`, `q3`) with `q2` removed.
    private func marker(authored: Int = 3, installed: Int = 2, quarantined: [Any] = ["q2"],
                        digest: String = PartialInstallTests.recordDigest) -> [String: Any] {
        ["authored_count": authored, "installed_count": installed, "quarantined_ids": quarantined, "record_digest": digest]
    }

    private func load(_ object: [String: Any]) throws -> PackManifest {
        try PackLoader().load(data: JSONSerialization.data(withJSONObject: object))
    }

    // MARK: - Full pack

    func testFullPackDecodesWithoutAMarkerAndCarriesNoPartialNote() throws {
        let manifest = try load(packObject(questionIDs: ["q1"]))
        XCTAssertNil(manifest.partialInstall)

        let pack = InstalledPack(courseID: "cissp", manifest: manifest)
        XCTAssertNil(pack.partialInstall)
        XCTAssertNil(pack.partialCoverageNote)
    }

    // MARK: - Valid partial pack

    func testValidPartialPackDecodesEncodesAndLabelsItselfFromTheMarker() throws {
        let manifest = try load(packObject(questionIDs: ["q1", "q3"], marker: marker()))
        let partial = try XCTUnwrap(manifest.partialInstall)
        XCTAssertEqual(partial, PartialInstall(
            authoredCount: 3, installedCount: 2, quarantinedIDs: ["q2"], recordDigest: Self.recordDigest
        ))

        // The marker survives encoding, so a re-encoded pack stays partial.
        let encoded = try JSONEncoder().encode(manifest)
        let encodedObject = try XCTUnwrap(JSONSerialization.jsonObject(with: encoded) as? [String: Any])
        let encodedMarker = try XCTUnwrap(encodedObject["partial_install"] as? [String: Any])
        XCTAssertEqual(encodedMarker["authored_count"] as? Int, 3)
        XCTAssertEqual(encodedMarker["installed_count"] as? Int, 2)
        XCTAssertEqual(encodedMarker["quarantined_ids"] as? [String], ["q2"])
        XCTAssertEqual(encodedMarker["record_digest"] as? String, Self.recordDigest)

        let pack = InstalledPack(courseID: "cissp", manifest: manifest)
        XCTAssertEqual(pack.partialCoverageNote, "Partial: 2 of 3 reviewed questions installed; 1 held for review")
    }

    // MARK: - Marker rules (mirrors marker_shape_reasons / L29)

    func testEachInvalidMarkerRuleIsRefused() throws {
        let cases: [(name: String, marker: [String: Any])] = [
            ("installed count disagrees with the questions", marker(installed: 3)),
            ("authored count does not exceed installed", marker(authored: 2)),
            ("quarantined ids are not unique", marker(quarantined: ["q2", "q2"])),
            ("quarantined id is still installed", marker(quarantined: ["q1"])),
            ("quarantined ids disagree with the counts", marker(quarantined: ["q2", "q4"])),
            ("quarantined id is blank", marker(quarantined: [""])),
            ("record digest is not sha256 hex", marker(digest: "sha256:XYZ")),
            ("record digest is not lowercase hex", marker(digest: "sha256:" + String(repeating: "A", count: 64))),
            ("installed count is negative", marker(installed: -1))
        ]
        for invalid in cases {
            XCTAssertThrowsError(
                try load(packObject(questionIDs: ["q1", "q3"], marker: invalid.marker)),
                invalid.name
            )
        }
    }

    func testANonObjectMarkerIsRefused() throws {
        var object = packObject(questionIDs: ["q1", "q3"])
        object["partial_install"] = [String]()
        XCTAssertThrowsError(try load(object))
    }

    func testUnknownOrMissingKeysInsideTheMarkerAreRefused() throws {
        var unknown = marker()
        unknown["reason"] = "held for accuracy"
        XCTAssertThrowsError(try load(packObject(questionIDs: ["q1", "q3"], marker: unknown)))

        var missing = marker()
        missing.removeValue(forKey: "record_digest")
        XCTAssertThrowsError(try load(packObject(questionIDs: ["q1", "q3"], marker: missing)))
    }

    // MARK: - Quarantined progress

    func testQuarantinedProgressIsIgnoredByInsightsButKeptInTheStore() throws {
        let manifest = try load(packObject(questionIDs: ["q1", "q3"], marker: marker()))
        let pack = InstalledPack(courseID: "cissp", manifest: manifest)
        let catalog = Dictionary(uniqueKeysWithValues: pack.questions.map { (pack.identity(for: $0), $0) })

        let installedID = pack.identity(for: try XCTUnwrap(pack.questions.first))
        let quarantinedID = QuestionIdentity(courseID: "cissp", packID: "partial-core", questionID: "q2")
        let quarantinedMastery = MasterySnapshot(identity: quarantinedID, answered: 4, correct: 1)
        let envelope = ProgressEnvelope(
            actorID: "test-actor",
            mastery: [
                MasterySnapshot(identity: installedID, answered: 2, correct: 2),
                quarantinedMastery
            ]
        )

        let insights = StudyInsights.derive(envelope: envelope, catalog: catalog, now: Date())

        // Coverage counts only the installed questions; q2's history is absent.
        XCTAssertEqual(insights.coverage, StudyCoverage(totalQuestions: 2, seen: 1, answered: 2, correct: 2))
        XCTAssertEqual(insights.due, StudyDueCounts(due: 0, upcoming: 0, unscheduled: 2))

        // The progress store itself is untouched: the quarantined question's
        // mastery survives verbatim for the day the pack is restored.
        XCTAssertEqual(envelope.mastery, [
            MasterySnapshot(identity: installedID, answered: 2, correct: 2),
            quarantinedMastery
        ])
    }
}
