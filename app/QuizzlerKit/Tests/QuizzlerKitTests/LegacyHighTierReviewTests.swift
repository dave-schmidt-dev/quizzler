import Foundation
import XCTest
@testable import QuizzlerKit

/// A legacy schema-1 question can sit at Leitner level 6 or 7, above the
/// level-5 default cap. Its first review must record the true prior level
/// while the clamp sets the resulting level and due date.
final class LegacyHighTierReviewTests: XCTestCase {
    private func temporaryFileURL() -> URL {
        let fileURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("QuizzlerKitTests-\(UUID().uuidString)", isDirectory: false)
        addTeardownBlock {
            try? FileManager.default.removeItem(at: fileURL)
        }
        return fileURL
    }

    private func assertLegacyHighTierReviewRecordsTruePrior(tier: Int) async throws {
        let reviewedAt = Date(timeIntervalSince1970: 10_000)
        let dueAt = reviewedAt.addingTimeInterval(120 * 86_400)
        let correctIdentity = QuestionIdentity(courseID: "course", packID: "pack", questionID: "legacy-correct-\(tier)")
        let missedIdentity = QuestionIdentity(courseID: "course", packID: "pack", questionID: "legacy-missed-\(tier)")
        let legacyState = try SRSState(
            tier: tier, nextDueAt: dueAt, lastReviewedAt: reviewedAt,
            intervalDays: tier == 6 ? 60 : 120, reviewCount: tier
        )
        let store = LocalProgressStore(fileURL: temporaryFileURL())
        try await store.write(ProgressEnvelope(
            schemaVersion: 1, actorID: "device-a",
            srs: [
                SRSSnapshot(identity: correctIdentity, state: legacyState),
                SRSSnapshot(identity: missedIdentity, state: legacyState)
            ]
        ))
        let repository = ProgressRepository(actorID: "device-a", store: store)
        let before = try await repository.snapshot()
        XCTAssertEqual(before.schemaVersion, 1)
        XCTAssertEqual(before.maximumLeitnerLevel, 5)
        XCTAssertEqual(before.srs.map(\.state.tier), [tier, tier])
        XCTAssertEqual(before.srs.map(\.state.nextDueAt), [dueAt, dueAt])

        let answeredAt = reviewedAt.addingTimeInterval(500)
        let correct = try await repository.save(SessionDetail(
            sessionID: "legacy-correct-\(tier)",
            completedAt: answeredAt.addingTimeInterval(10),
            answers: [SessionAnswer(identity: correctIdentity, correct: true, answeredAt: answeredAt)]
        ), now: answeredAt.addingTimeInterval(60))
        let correctEvents = ProgressEnvelope.reviewEvents(for: correct, from: before)
        XCTAssertEqual(correctEvents.count, 1)
        XCTAssertEqual(correctEvents[0].outcome, .correct)
        XCTAssertEqual(correctEvents[0].priorLevel, tier)
        XCTAssertEqual(correctEvents[0].resultingLevel, 5)
        XCTAssertEqual(correctEvents[0].resultingDueAt, answeredAt.addingTimeInterval(30 * 86_400))

        let afterCorrect = try await repository.snapshot()
        XCTAssertEqual(afterCorrect.schemaVersion, 2)
        XCTAssertEqual(afterCorrect.srs.first(where: { $0.identity == correctIdentity })?.state.tier, 5)
        XCTAssertEqual(
            afterCorrect.srs.first(where: { $0.identity == correctIdentity })?.state.nextDueAt,
            answeredAt.addingTimeInterval(30 * 86_400)
        )
        XCTAssertEqual(afterCorrect.srs.first(where: { $0.identity == missedIdentity })?.state.tier, tier)

        let missedAt = answeredAt.addingTimeInterval(1_000)
        let missed = try await repository.save(SessionDetail(
            sessionID: "legacy-missed-\(tier)",
            completedAt: missedAt.addingTimeInterval(10),
            answers: [SessionAnswer(identity: missedIdentity, correct: false, answeredAt: missedAt)]
        ), now: missedAt.addingTimeInterval(60))
        let missedEvents = ProgressEnvelope.reviewEvents(for: missed, from: afterCorrect)
        XCTAssertEqual(missedEvents.count, 1)
        XCTAssertEqual(missedEvents[0].outcome, .missed)
        XCTAssertEqual(missedEvents[0].priorLevel, tier)
        XCTAssertEqual(missedEvents[0].resultingLevel, 3)
        XCTAssertEqual(missedEvents[0].resultingDueAt, missedAt.addingTimeInterval(7 * 86_400))

        let afterMissed = try await repository.snapshot()
        XCTAssertEqual(afterMissed.srs.first(where: { $0.identity == missedIdentity })?.state.tier, 3)
        XCTAssertEqual(
            afterMissed.srs.first(where: { $0.identity == missedIdentity })?.state.nextDueAt,
            missedAt.addingTimeInterval(7 * 86_400)
        )
    }

    func testLegacyTierSixReviewRecordsTruePriorLevel() async throws {
        try await assertLegacyHighTierReviewRecordsTruePrior(tier: 6)
    }

    func testLegacyTierSevenReviewRecordsTruePriorLevel() async throws {
        try await assertLegacyHighTierReviewRecordsTruePrior(tier: 7)
    }
}
