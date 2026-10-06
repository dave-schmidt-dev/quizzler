import Foundation
import XCTest
@testable import QuizzlerKit

final class SessionResumeTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_700_000_000)
    private let fingerprint = "sha256:fixed"

    // MARK: - Helpers

    private func identity(_ id: String) -> QuestionIdentity {
        QuestionIdentity(courseID: "cissp", packID: "core", questionID: id)
    }

    private func answer(_ id: String, correct: Bool = true) -> SessionAnswer {
        SessionAnswer(courseID: "cissp", packID: "core", questionID: id, correct: correct)
    }

    private func session(
        mode: SelectionMode = .normal,
        plan: [QuestionIdentity],
        position: Int = 0,
        answers: [SessionAnswer] = [],
        baseline: [QuestionIdentity: Int] = [:],
        schemaVersion: Int = PersistedSession.currentSchemaVersion,
        packFingerprint: String = "sha256:fixed"
    ) -> PersistedSession {
        PersistedSession(
            schemaVersion: schemaVersion,
            courseID: "cissp",
            packID: "core",
            packFingerprint: packFingerprint,
            mode: mode,
            plan: plan,
            position: position,
            answers: answers,
            startBaseline: baseline.map { BaselineEntry(identity: $0.key, answeredCount: $0.value) }
        )
    }

    private func resolve(
        _ saved: PersistedSession,
        catalog: Set<QuestionIdentity>,
        answeredCounts: [QuestionIdentity: Int] = [:],
        dueDates: [QuestionIdentity: Date] = [:]
    ) -> SessionResume.Resolution {
        SessionResume.resolve(
            saved: saved,
            currentFingerprint: fingerprint,
            catalog: catalog,
            answeredCounts: answeredCounts,
            dueDates: dueDates,
            now: now
        )
    }

    private func expectDiscard(
        _ saved: PersistedSession,
        catalog: Set<QuestionIdentity>,
        answeredCounts: [QuestionIdentity: Int] = [:],
        dueDates: [QuestionIdentity: Date] = [:],
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        let resolution = resolve(saved, catalog: catalog, answeredCounts: answeredCounts, dueDates: dueDates)
        guard case .discard = resolution else {
            XCTFail("expected discard, got \(resolution)", file: file, line: line)
            return
        }
    }

    private func expectResumable(
        _ saved: PersistedSession,
        catalog: Set<QuestionIdentity>,
        answeredCounts: [QuestionIdentity: Int] = [:],
        dueDates: [QuestionIdentity: Date] = [:],
        file: StaticString = #filePath,
        line: UInt = #line
    ) -> SessionResume.ResumableSession {
        let resolution = resolve(saved, catalog: catalog, answeredCounts: answeredCounts, dueDates: dueDates)
        guard case .resumable(let resumable) = resolution else {
            XCTFail("expected resumable, got \(resolution)", file: file, line: line)
            return SessionResume.ResumableSession(
                plan: [], position: 0, answers: [], mode: saved.mode,
                newIdentities: [], scheduledStates: []
            )
        }
        return resumable
    }

    // MARK: - Codable round trip

    func testPersistedSessionRoundTripsThroughJSON() throws {
        let scheduled = ScheduledEntry(identity: identity("q1"), state: try SRSState(tier: 2, nextDueAt: now))
        let session = PersistedSession(
            courseID: "cissp",
            packID: "core",
            packFingerprint: "sha256:abc",
            mode: .srs,
            plan: [identity("q0"), identity("q1")],
            position: 1,
            answers: [answer("q0", correct: false)],
            newIdentities: [identity("q0")],
            startedAt: now,
            updatedAt: now,
            startBaseline: [BaselineEntry(identity: identity("q0"), answeredCount: 0)],
            scheduledStates: [scheduled]
        )
        XCTAssertEqual(session.packKey, "cissp/core")

        let data = try JSONEncoder().encode(session)
        let decoded = try JSONDecoder().decode(PersistedSession.self, from: data)
        XCTAssertEqual(decoded, session)
    }

    // MARK: - Discard rules

    func testSchemaMismatchDiscards() {
        let saved = session(
            plan: [identity("q0")],
            schemaVersion: PersistedSession.currentSchemaVersion + 1
        )
        expectDiscard(saved, catalog: [identity("q0")])
    }

    func testFingerprintMismatchDiscards() {
        let saved = session(plan: [identity("q0")], packFingerprint: "sha256:other")
        expectDiscard(saved, catalog: [identity("q0")])
    }

    func testAllRemainingDroppedDiscards() {
        // q1 is the only unanswered question and was answered elsewhere.
        let saved = session(
            plan: [identity("q0"), identity("q1")],
            position: 1,
            answers: [answer("q0")],
            baseline: [identity("q1"): 0]
        )
        expectDiscard(
            saved,
            catalog: [identity("q0"), identity("q1")],
            answeredCounts: [identity("q1"): 1]
        )
    }

    // MARK: - Filtering rules

    func testQuestionAnsweredElsewhereIsDroppedAndOthersSurviveInOrder() {
        let q0 = identity("q0")
        let q1 = identity("q1")
        let q2 = identity("q2")
        let saved = session(
            plan: [q0, q1, q2],
            position: 1,
            answers: [answer("q0")],
            baseline: [q1: 1, q2: 0]
        )
        // q1's current count (2) exceeds its baseline (1): answered elsewhere.
        let resumable = expectResumable(
            saved,
            catalog: [q0, q1, q2],
            answeredCounts: [q1: 2]
        )
        XCTAssertEqual(resumable.plan, [q0, q2])
        XCTAssertEqual(resumable.position, 1)
        XCTAssertEqual(resumable.answers, [answer("q0")])
    }

    func testSavedAnswerAtCurrentPositionIsSkippedNeverReserved() {
        // The app saved after recording q1 but before advancing the position.
        let saved = session(
            plan: [identity("q0"), identity("q1"), identity("q2")],
            position: 1,
            answers: [answer("q0"), answer("q1")]
        )
        let resumable = expectResumable(
            saved,
            catalog: [identity("q0"), identity("q1"), identity("q2")]
        )
        XCTAssertEqual(resumable.plan, [identity("q0"), identity("q1"), identity("q2")])
        XCTAssertEqual(resumable.position, 2)
        XCTAssertEqual(resumable.plan[2], identity("q2"))
    }

    func testSRSNotYetDueIsDropped() {
        let q0 = identity("q0")
        let q1 = identity("q1")
        let q2 = identity("q2")
        let saved = session(mode: .srs, plan: [q0, q1, q2])
        let resumable = expectResumable(
            saved,
            catalog: [q0, q1, q2],
            dueDates: [q0: now.addingTimeInterval(-60), q1: now.addingTimeInterval(3_600), q2: now]
        )
        // q1 is due in an hour; a due date exactly at now stays servable.
        XCTAssertEqual(resumable.plan, [q0, q2])
        XCTAssertEqual(resumable.position, 0)
    }

    func testDueDatesAreIgnoredOutsideSRSMode() {
        let q0 = identity("q0")
        let saved = session(mode: .normal, plan: [q0])
        let resumable = expectResumable(
            saved,
            catalog: [q0],
            dueDates: [q0: now.addingTimeInterval(3_600)]
        )
        XCTAssertEqual(resumable.plan, [q0])
        XCTAssertEqual(resumable.position, 0)
    }

    func testQuestionMissingFromCatalogIsDropped() {
        let q0 = identity("q0")
        let q1 = identity("q1")
        let saved = session(plan: [q0, q1])
        let resumable = expectResumable(saved, catalog: [q0])
        XCTAssertEqual(resumable.plan, [q0])
        XCTAssertEqual(resumable.position, 0)
    }

    func testAnsweredPrefixIsKeptInPlan() {
        let q0 = identity("q0")
        let q1 = identity("q1")
        let q2 = identity("q2")
        let saved = session(
            plan: [q0, q1, q2],
            position: 2,
            answers: [answer("q0"), answer("q1", correct: false)]
        )
        let resumable = expectResumable(saved, catalog: [q0, q1, q2])
        XCTAssertEqual(resumable.plan, [q0, q1, q2])
        XCTAssertEqual(resumable.position, 2)
        XCTAssertEqual(resumable.answers, [answer("q0"), answer("q1", correct: false)])
    }
}
