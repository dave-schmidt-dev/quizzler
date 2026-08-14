import XCTest
@testable import QuizzlerKit

final class MigrationReconciliationTests: XCTestCase {
    private let inventoryHash = String(repeating: "a", count: 64)

    func testNewStartIsEmptyAndBindsRevisionZeroBaseline() throws {
        let envelope = try MigrationEnvelope.newStart(
            migrationEpoch: "epoch-new-start",
            sourceSnapshotHash: inventoryHash,
            activePackIDs: ["cissp"]
        )

        XCTAssertTrue(envelope.isNewStart)
        try envelope.verifyNewStart()
        XCTAssertEqual(envelope.counts, try MigrationSourceCounts(sources: 0, records: 0))
        XCTAssertEqual(envelope.progress.documentRevision, 0)
        XCTAssertTrue(envelope.progress.operations.isEmpty)
        XCTAssertFalse(envelope.cloudKitBaseline.importClaim)
        XCTAssertEqual(envelope.cloudKitBaseline.documentRevision, 0)
    }

    func testNewStartRejectsSourceCountsOrImportClaim() throws {
        let progress = ProgressEnvelope(actorID: "migration-baseline", operationID: "baseline")
        let baseline = try MigrationCloudKitBaseline(
            semanticHash: String(repeating: "b", count: 64),
            importClaim: true
        )
        XCTAssertThrowsError(try MigrationEnvelope(
            migrationEpoch: "epoch",
            path: .newStart,
            sourceSnapshotHash: inventoryHash,
            counts: try MigrationSourceCounts(sources: 0, records: 1),
            activePackIDs: ["cissp"],
            progress: progress,
            cloudKitBaseline: baseline
        )) { error in
            XCTAssertEqual(error as? MigrationEnvelopeError, .newStartMustBeEmpty)
        }
    }

    func testCodableRoundTripPreservesMigrationBoundary() throws {
        let original = try MigrationEnvelope.newStart(
            migrationEpoch: "epoch-round-trip",
            sourceSnapshotHash: inventoryHash,
            activePackIDs: ["cissp"]
        )
        let data = try JSONEncoder().encode(original)
        let decoded = try JSONDecoder().decode(MigrationEnvelope.self, from: data)
        XCTAssertEqual(decoded, original)
    }

    func testNewStartSemanticHashMatchesSharedFixture() throws {
        let envelope = try MigrationEnvelope.newStart(
            migrationEpoch: "epoch-fixture",
            sourceSnapshotHash: String(repeating: "a", count: 64),
            activePackIDs: ["cissp"]
        )
        XCTAssertEqual(
            envelope.cloudKitBaseline.semanticHash,
            "6f7243c159313fb29c4c2ac9b8b0e2f9f0a4a9b5fea3e98b19b2bf6670b43d30"
        )
    }

    func testCodableUsesSharedSnakeCaseBaselineKeys() throws {
        let envelope = try MigrationEnvelope.newStart(
            migrationEpoch: "epoch-fixture",
            sourceSnapshotHash: inventoryHash,
            activePackIDs: ["cissp"]
        )
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: JSONEncoder().encode(envelope)) as? [String: Any]
        )
        XCTAssertEqual(
            Set(object.keys),
            ["schema_version", "migration_epoch", "path", "source_snapshot_hash",
             "source_export_hash", "counts", "scope", "document", "cloudkit_baseline"]
        )
        let baseline = try XCTUnwrap(object["cloudkit_baseline"] as? [String: Any])
        XCTAssertEqual(
            Set(baseline.keys),
            ["document_revision", "operation_id", "semantic_hash", "import_claim"]
        )
    }

    func testInvalidHashAndEmptyScopeFailClosed() throws {
        XCTAssertThrowsError(try MigrationEnvelope.newStart(
            migrationEpoch: "epoch",
            sourceSnapshotHash: "not-a-hash",
            activePackIDs: ["cissp"]
        ))
        XCTAssertThrowsError(try MigrationEnvelope.newStart(
            migrationEpoch: "epoch",
            sourceSnapshotHash: inventoryHash,
            activePackIDs: []
        ))
    }
}
