import Foundation
import XCTest
@testable import QuizzlerKit

final class StudySessionTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_700_000_000)

    // MARK: - Helpers

    private func makeQuestion(id: String, area: String = "Security Operations", topic: String = "Ops") -> Question {
        .multipleChoice(MultipleChoiceQuestion(
            id: id,
            metadata: QuestionMetadata(topic: topic, examArea: area, difficulty: .easy),
            prompt: "Prompt \(id)",
            explanation: "Explanation \(id)",
            options: ["A", "B"],
            answer: 0
        ))
    }

    private func identity(for id: String, packID: String = "core", courseID: String = "cissp") -> QuestionIdentity {
        QuestionIdentity(courseID: courseID, packID: packID, questionID: id)
    }

    // MARK: - 1. SelectionRequest Validation

    func testSelectionRequestThrowsOnNonPositiveLimit() {
        XCTAssertThrowsError(try SelectionRequest(mode: .normal, limit: 0)) { error in
            XCTAssertEqual(error as? SRSContractError, .invalidLimit)
        }
        XCTAssertThrowsError(try SelectionRequest(mode: .srs, limit: -1)) { error in
            XCTAssertEqual(error as? SRSContractError, .invalidLimit)
        }
        XCTAssertThrowsError(try SelectionRequest(mode: .retryMissed, limit: -10)) { error in
            XCTAssertEqual(error as? SRSContractError, .invalidLimit)
        }
        XCTAssertThrowsError(try SelectionRequest(mode: .weakAreas, limit: 0)) { error in
            XCTAssertEqual(error as? SRSContractError, .invalidLimit)
        }

        XCTAssertNoThrow(try SelectionRequest(mode: .weakAreas, limit: 5))
        XCTAssertEqual(SelectionMode.weakAreas.rawValue, "weak_areas")
    }

    // MARK: - 2. .normal mode semantics

    func testNormalModePackOrderAndResumeIndexWrappingAndNormalization() throws {
        let ids = (0..<5).map { identity(for: "q\($0)") }
        var catalog: [QuestionIdentity: Question] = [:]
        for id in ids {
            catalog[id] = makeQuestion(id: id.questionID)
        }

        let req3 = try SelectionRequest(mode: .normal, limit: 3)
        let plan0 = StudySessionPlan.build(
            request: req3,
            envelope: nil,
            catalog: catalog,
            packOrder: ids,
            resumeIndex: 0,
            now: now
        )
        XCTAssertEqual(plan0.mode, .normal)
        XCTAssertEqual(plan0.questions, [ids[0], ids[1], ids[2]])

        let req4 = try SelectionRequest(mode: .normal, limit: 4)
        let planWrap = StudySessionPlan.build(
            request: req4,
            envelope: nil,
            catalog: catalog,
            packOrder: ids,
            resumeIndex: 3,
            now: now
        )
        // Starts at index 3, wraps past the end: [q3, q4, q0, q1]
        XCTAssertEqual(planWrap.questions, [ids[3], ids[4], ids[0], ids[1]])

        // Oversized resumeIndex: 7 % 5 = 2 -> starts at index 2: [q2, q3, q4]
        let planOversized = StudySessionPlan.build(
            request: req3,
            envelope: nil,
            catalog: catalog,
            packOrder: ids,
            resumeIndex: 7,
            now: now
        )
        XCTAssertEqual(planOversized.questions, [ids[2], ids[3], ids[4]])

        // Negative resumeIndex: -1 normalized is 4 -> starts at index 4: [q4, q0, q1]
        let planNegative1 = StudySessionPlan.build(
            request: req3,
            envelope: nil,
            catalog: catalog,
            packOrder: ids,
            resumeIndex: -1,
            now: now
        )
        XCTAssertEqual(planNegative1.questions, [ids[4], ids[0], ids[1]])

        // Negative resumeIndex: -6 normalized is 4 -> starts at index 4
        let req2 = try SelectionRequest(mode: .normal, limit: 2)
        let planNegative6 = StudySessionPlan.build(
            request: req2,
            envelope: nil,
            catalog: catalog,
            packOrder: ids,
            resumeIndex: -6,
            now: now
        )
        XCTAssertEqual(planNegative6.questions, [ids[4], ids[0]])
    }

    func testNormalModeIgnoresOffCatalogPackOrderQuestions() throws {
        let id0 = identity(for: "q0")
        let id1 = identity(for: "q1")
        let id2 = identity(for: "q2")
        let idOff = identity(for: "qOff")

        let catalog = [
            id0: makeQuestion(id: "q0"),
            id1: makeQuestion(id: "q1"),
            id2: makeQuestion(id: "q2")
        ]
        let packOrder = [id0, idOff, id1, id2]

        let req = try SelectionRequest(mode: .normal, limit: 3)
        let plan = StudySessionPlan.build(
            request: req,
            envelope: nil,
            catalog: catalog,
            packOrder: packOrder,
            resumeIndex: 0,
            now: now
        )
        XCTAssertEqual(plan.questions, [id0, id1, id2])
    }

    // MARK: - 3. .srs mode semantics

    func testSRSModeOrdersMostOverdueFirstIncludesNowAndEmptyWhenNothingDue() throws {
        let idOverdue2h = identity(for: "overdue2h")
        let idOverdue1h = identity(for: "overdue1h")
        let idDueNowA = identity(for: "dueNowA")
        let idDueNowB = identity(for: "dueNowB")
        let idUpcoming = identity(for: "upcoming")
        let idUnscheduled = identity(for: "unscheduled")

        let catalog = [
            idOverdue2h: makeQuestion(id: "overdue2h"),
            idOverdue1h: makeQuestion(id: "overdue1h"),
            idDueNowA: makeQuestion(id: "dueNowA"),
            idDueNowB: makeQuestion(id: "dueNowB"),
            idUpcoming: makeQuestion(id: "upcoming"),
            idUnscheduled: makeQuestion(id: "unscheduled")
        ]

        let srsSnapshots = [
            SRSSnapshot(identity: idOverdue1h, state: try SRSState(nextDueAt: now.addingTimeInterval(-3600))),
            SRSSnapshot(identity: idDueNowB, state: try SRSState(nextDueAt: now)),
            SRSSnapshot(identity: idOverdue2h, state: try SRSState(nextDueAt: now.addingTimeInterval(-7200))),
            SRSSnapshot(identity: idDueNowA, state: try SRSState(nextDueAt: now)),
            SRSSnapshot(identity: idUpcoming, state: try SRSState(nextDueAt: now.addingTimeInterval(3600)))
        ]

        let envelope = ProgressEnvelope(actorID: "test-actor", srs: srsSnapshots)
        let reqAll = try SelectionRequest(mode: .srs, limit: 10)
        let plan = StudySessionPlan.build(
            request: reqAll,
            envelope: envelope,
            catalog: catalog,
            packOrder: [],
            now: now
        )

        XCTAssertEqual(plan.mode, .srs)
        // Most overdue first (ascending nextDueAt), tie-broken by identity.description ascending:
        // overdue2h (-7200s), overdue1h (-3600s), dueNowA (now), dueNowB (now)
        XCTAssertEqual(plan.questions, [idOverdue2h, idOverdue1h, idDueNowA, idDueNowB])

        // Empty plan when nothing is due
        let futureEnvelope = ProgressEnvelope(
            actorID: "test-actor",
            srs: [SRSSnapshot(identity: idUpcoming, state: try SRSState(nextDueAt: now.addingTimeInterval(3600)))]
        )
        let futurePlan = StudySessionPlan.build(
            request: reqAll,
            envelope: futureEnvelope,
            catalog: catalog,
            packOrder: [],
            now: now
        )
        XCTAssertTrue(futurePlan.questions.isEmpty)

        // Empty plan when envelope is nil
        let nilPlan = StudySessionPlan.build(
            request: reqAll,
            envelope: nil,
            catalog: catalog,
            packOrder: [],
            now: now
        )
        XCTAssertTrue(nilPlan.questions.isEmpty)
    }

    // MARK: - 4. .retryMissed mode semantics

    func testRetryMissedMatchesStudyInsightsMissedQueue() throws {
        let idRecent = identity(for: "recent")
        let idHistHigh = identity(for: "histHigh")
        let idHistMid = identity(for: "histMid")
        let idPerfect = identity(for: "perfect")

        let catalog = [
            idRecent: makeQuestion(id: "recent"),
            idHistHigh: makeQuestion(id: "histHigh"),
            idHistMid: makeQuestion(id: "histMid"),
            idPerfect: makeQuestion(id: "perfect")
        ]

        let session = SessionDetail(
            sessionID: "s1",
            completedAt: now,
            answers: [SessionAnswer(identity: idRecent, correct: false)]
        )
        let envelope = ProgressEnvelope(
            actorID: "test-actor",
            sessionDetails: [session],
            mastery: [
                MasterySnapshot(identity: idRecent, answered: 1, correct: 0),
                MasterySnapshot(identity: idHistHigh, answered: 10, correct: 2),
                MasterySnapshot(identity: idHistMid, answered: 5, correct: 2),
                MasterySnapshot(identity: idPerfect, answered: 5, correct: 5)
            ]
        )

        for limit in [1, 2, 3, 5] {
            let req = try SelectionRequest(mode: .retryMissed, limit: limit)
            let plan = StudySessionPlan.build(
                request: req,
                envelope: envelope,
                catalog: catalog,
                packOrder: [],
                now: now
            )
            let expected = StudyInsights.missedQueue(envelope: envelope, catalog: catalog, limit: limit)
            XCTAssertEqual(plan.questions, expected)
            XCTAssertEqual(plan.mode, .retryMissed)
        }
    }

    // MARK: - 5. .weakAreas interleaving and ordering

    func testWeakAreasInterleavesAcrossAreasWeakestFirst() throws {
        // Setup 3 areas:
        // Area 1: "Crypto" -> accuracy 2/10 = 0.20 (weakest)
        // Area 2: "Network" -> accuracy 5/10 = 0.50 (middle)
        // Area 3: "Governance" -> accuracy 8/10 = 0.80 (strongest)
        let idC_unans = identity(for: "c_unanswered")
        let idC_acc25 = identity(for: "c_acc25")
        let idC_acc50_b = identity(for: "c_acc50_b")
        let idC_acc50_a = identity(for: "c_acc50_a")

        let idN_1 = identity(for: "n_1")
        let idN_2 = identity(for: "n_2")

        let idG_1 = identity(for: "g_1")
        let idG_2 = identity(for: "g_2")

        let catalog = [
            idC_unans: makeQuestion(id: "c_unanswered", area: "Crypto"),
            idC_acc25: makeQuestion(id: "c_acc25", area: "Crypto"),
            idC_acc50_b: makeQuestion(id: "c_acc50_b", area: "Crypto"),
            idC_acc50_a: makeQuestion(id: "c_acc50_a", area: "Crypto"),
            idN_1: makeQuestion(id: "n_1", area: "Network"),
            idN_2: makeQuestion(id: "n_2", area: "Network"),
            idG_1: makeQuestion(id: "g_1", area: "Governance"),
            idG_2: makeQuestion(id: "g_2", area: "Governance")
        ]

        let mastery = [
            // Crypto questions
            MasterySnapshot(identity: idC_acc25, answered: 4, correct: 1), // acc 0.25
            MasterySnapshot(identity: idC_acc50_b, answered: 2, correct: 1), // acc 0.50
            MasterySnapshot(identity: idC_acc50_a, answered: 4, correct: 2), // acc 0.50
            // idC_unans is unattempted (never-answered: answered 0, correct 0)
            // Network questions
            MasterySnapshot(identity: idN_1, answered: 5, correct: 2),
            MasterySnapshot(identity: idN_2, answered: 5, correct: 3),
            // Governance questions
            MasterySnapshot(identity: idG_1, answered: 5, correct: 4),
            MasterySnapshot(identity: idG_2, answered: 5, correct: 4)
        ]

        let envelope = ProgressEnvelope(actorID: "test-actor", mastery: mastery)

        // Verify ordering within Crypto area:
        // 1. never-answered: idC_unans
        // 2. ascending accuracy: idC_acc25 (0.25)
        // 3. accuracy 0.50 tied: idC_acc50_a ("...c_acc50_a" < "...c_acc50_b")
        // 4. idC_acc50_b

        // Round-robin with limit 5:
        // Round 0: Crypto (idC_unans), Network (idN_1), Governance (idG_1)
        // Round 1: Crypto (idC_acc25), Network (idN_2)
        // Total: 5 questions
        let req5 = try SelectionRequest(mode: .weakAreas, limit: 5)
        let plan5 = StudySessionPlan.build(
            request: req5,
            envelope: envelope,
            catalog: catalog,
            packOrder: [],
            now: now
        )

        XCTAssertEqual(plan5.mode, .weakAreas)
        XCTAssertEqual(plan5.questions.count, 5)

        // Interleaving assertion: batch contains questions from more than one area
        let areasInBatch = Set(plan5.questions.compactMap { catalog[$0]?.metadata.examArea })
        XCTAssertEqual(areasInBatch, Set(["Crypto", "Network", "Governance"]))

        // Weakest area contributes the most questions (2 for Crypto, 2 for Network, 1 for Governance)
        let cryptoCount5 = plan5.questions.filter { catalog[$0]?.metadata.examArea == "Crypto" }.count
        let govCount5 = plan5.questions.filter { catalog[$0]?.metadata.examArea == "Governance" }.count
        XCTAssertGreaterThan(cryptoCount5, govCount5)

        // Check exact interleaving order
        XCTAssertEqual(plan5.questions, [idC_unans, idN_1, idG_1, idC_acc25, idN_2])

        // Round-robin with limit 7:
        // Round 0: Crypto (idC_unans), Network (idN_1), Governance (idG_1)
        // Round 1: Crypto (idC_acc25), Network (idN_2), Governance (idG_2)
        // Round 2: Crypto (idC_acc50_a)
        // Network & Governance are now exhausted.
        let req7 = try SelectionRequest(mode: .weakAreas, limit: 7)
        let plan7 = StudySessionPlan.build(
            request: req7,
            envelope: envelope,
            catalog: catalog,
            packOrder: [],
            now: now
        )

        XCTAssertEqual(plan7.questions, [idC_unans, idN_1, idG_1, idC_acc25, idN_2, idG_2, idC_acc50_a])
        let cryptoCount7 = plan7.questions.filter { catalog[$0]?.metadata.examArea == "Crypto" }.count
        let networkCount7 = plan7.questions.filter { catalog[$0]?.metadata.examArea == "Network" }.count
        let govCount7 = plan7.questions.filter { catalog[$0]?.metadata.examArea == "Governance" }.count
        XCTAssertEqual(cryptoCount7, 3)
        XCTAssertEqual(networkCount7, 2)
        XCTAssertEqual(govCount7, 2)
        XCTAssertTrue(cryptoCount7 > networkCount7 && cryptoCount7 > govCount7)
    }

    // MARK: - 6. Batch cap holds for every mode and larger queue not truncated

    func testBatchCapHoldsForEveryModeAndLargerQueueNotTruncated() throws {
        let ids = (0..<6).map { identity(for: "q\($0)") }
        var catalog: [QuestionIdentity: Question] = [:]
        for (i, id) in ids.enumerated() {
            catalog[id] = makeQuestion(id: id.questionID, area: "Area\(i % 2)")
        }

        let srsSnapshots = ids.map { id in
            SRSSnapshot(identity: id, state: try! SRSState(nextDueAt: now.addingTimeInterval(-3600)))
        }
        let mastery = ids.enumerated().map { i, id in
            MasterySnapshot(identity: id, answered: 5, correct: i)
        }
        let session = SessionDetail(
            sessionID: "s1",
            completedAt: now,
            answers: ids.map { SessionAnswer(identity: $0, correct: false) }
        )
        let envelope = ProgressEnvelope(
            actorID: "test-actor",
            sessionDetails: [session],
            mastery: mastery,
            srs: srsSnapshots
        )

        for mode in SelectionMode.allCases {
            // Small limit caps the batch
            let req2 = try SelectionRequest(mode: mode, limit: 2)
            let plan2 = StudySessionPlan.build(
                request: req2,
                envelope: envelope,
                catalog: catalog,
                packOrder: ids,
                now: now
            )
            XCTAssertEqual(plan2.questions.count, 2, "Mode \(mode) should return exactly 2 questions")

            // Bigger limit returns the additional questions (not truncated)
            let req4 = try SelectionRequest(mode: mode, limit: 4)
            let plan4 = StudySessionPlan.build(
                request: req4,
                envelope: envelope,
                catalog: catalog,
                packOrder: ids,
                now: now
            )
            XCTAssertEqual(plan4.questions.count, 4, "Mode \(mode) should return exactly 4 questions")
            XCTAssertEqual(Array(plan4.questions.prefix(2)), plan2.questions, "Mode \(mode) prefix should match smaller batch")

            // Limit larger than available returns all available without duplicates or padding
            let req20 = try SelectionRequest(mode: mode, limit: 20)
            let plan20 = StudySessionPlan.build(
                request: req20,
                envelope: envelope,
                catalog: catalog,
                packOrder: ids,
                now: now
            )
            XCTAssertEqual(plan20.questions.count, 6, "Mode \(mode) should return all 6 available questions")
            XCTAssertEqual(Set(plan20.questions).count, 6, "Mode \(mode) must not return duplicates")
        }
    }

    // MARK: - 7. Excluding removes identity before limit

    func testExcludingRemovesIdentityAndBatchStillReachesLimit() throws {
        let ids = (0..<6).map { identity(for: "q\($0)") }
        var catalog: [QuestionIdentity: Question] = [:]
        for (i, id) in ids.enumerated() {
            catalog[id] = makeQuestion(id: id.questionID, area: "Area\(i % 2)")
        }

        let srsSnapshots = ids.map { id in
            SRSSnapshot(identity: id, state: try! SRSState(nextDueAt: now.addingTimeInterval(-3600)))
        }
        let mastery = ids.enumerated().map { i, id in
            MasterySnapshot(identity: id, answered: 5, correct: i)
        }
        let session = SessionDetail(
            sessionID: "s1",
            completedAt: now,
            answers: ids.map { SessionAnswer(identity: $0, correct: false) }
        )
        let envelope = ProgressEnvelope(
            actorID: "test-actor",
            sessionDetails: [session],
            mastery: mastery,
            srs: srsSnapshots
        )

        for mode in SelectionMode.allCases {
            let req = try SelectionRequest(mode: mode, limit: 3)
            // First get the baseline plan without exclusion
            let baseline = StudySessionPlan.build(
                request: req,
                envelope: envelope,
                catalog: catalog,
                packOrder: ids,
                now: now
            )
            XCTAssertEqual(baseline.questions.count, 3)
            let excludedID = baseline.questions[0]

            // Now build excluding the first question: batch must still reach limit 3
            let planWithExclusion = StudySessionPlan.build(
                request: req,
                envelope: envelope,
                catalog: catalog,
                packOrder: ids,
                excluding: [excludedID],
                now: now
            )
            XCTAssertEqual(planWithExclusion.questions.count, 3, "Mode \(mode) should still reach limit 3")
            XCTAssertFalse(planWithExclusion.questions.contains(excludedID), "Mode \(mode) must not contain excluded question")
        }
    }

    // MARK: - 8. Empty catalog and empty packOrder

    func testEmptyCatalogAndEmptyPackOrder() throws {
        let id0 = identity(for: "q0")
        let catalog = [id0: makeQuestion(id: "q0")]
        let srsSnapshots = [SRSSnapshot(identity: id0, state: try SRSState(nextDueAt: now.addingTimeInterval(-3600)))]
        let envelope = ProgressEnvelope(actorID: "test-actor", srs: srsSnapshots)

        for mode in SelectionMode.allCases {
            let req = try SelectionRequest(mode: mode, limit: 5)

            // Empty catalog
            let emptyCatalogPlan = StudySessionPlan.build(
                request: req,
                envelope: envelope,
                catalog: [:],
                packOrder: [id0],
                now: now
            )
            XCTAssertTrue(emptyCatalogPlan.questions.isEmpty, "Mode \(mode) with empty catalog should yield empty plan")

            // Empty packOrder with non-empty catalog
            let emptyPackOrderPlan = StudySessionPlan.build(
                request: req,
                envelope: envelope,
                catalog: catalog,
                packOrder: [],
                now: now
            )
            if mode == .normal {
                XCTAssertTrue(emptyPackOrderPlan.questions.isEmpty, "Normal mode with empty packOrder should be empty")
            } else if mode == .srs {
                XCTAssertEqual(emptyPackOrderPlan.questions, [id0], "SRS mode does not depend on packOrder")
            } else if mode == .weakAreas {
                XCTAssertEqual(emptyPackOrderPlan.questions, [id0], "WeakAreas mode does not depend on packOrder")
            }
        }
    }

    // MARK: - 9. No duplicates within a plan

    func testNoDuplicatesWithinAPlanAcrossAllModes() throws {
        let id0 = identity(for: "q0")
        let id1 = identity(for: "q1")
        let catalog = [
            id0: makeQuestion(id: "q0", area: "A"),
            id1: makeQuestion(id: "q1", area: "B")
        ]
        // packOrder has deliberate duplicates
        let duplicatePackOrder = [id0, id1, id0, id1]
        let srsSnapshots = [
            SRSSnapshot(identity: id0, state: try SRSState(nextDueAt: now.addingTimeInterval(-3600))),
            SRSSnapshot(identity: id0, state: try SRSState(nextDueAt: now.addingTimeInterval(-1800))),
            SRSSnapshot(identity: id1, state: try SRSState(nextDueAt: now.addingTimeInterval(-3600)))
        ]
        let envelope = ProgressEnvelope(actorID: "test-actor", srs: srsSnapshots)

        for mode in SelectionMode.allCases {
            let req = try SelectionRequest(mode: mode, limit: 10)
            let plan = StudySessionPlan.build(
                request: req,
                envelope: envelope,
                catalog: catalog,
                packOrder: duplicatePackOrder,
                now: now
            )
            XCTAssertEqual(Set(plan.questions).count, plan.questions.count, "Mode \(mode) produced duplicate questions")
        }
    }
}
