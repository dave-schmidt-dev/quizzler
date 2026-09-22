import Foundation
import XCTest
@testable import QuizzlerKit

final class StudyInsightsTests: XCTestCase {
    private let calendar = Calendar(identifier: .gregorian)
    private let now = Date(timeIntervalSince1970: 1_700_000_000) // Deterministic reference date

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

    // MARK: - 1. Question.metadata accessor

    func testQuestionMetadataAccessorReturnsMetadataForAllCases() {
        let meta = QuestionMetadata(topic: "Topic", examArea: "Area", difficulty: .medium)

        let mc = Question.multipleChoice(MultipleChoiceQuestion(
            id: "mc", metadata: meta, prompt: "P", explanation: "E", options: ["A", "B"], answer: 0
        ))
        XCTAssertEqual(mc.metadata, meta)

        let smc = Question.scenarioMultipleChoice(ScenarioMultipleChoiceQuestion(
            id: "smc", metadata: meta, prompt: "P", explanation: "E", options: ["A", "B"], answer: 0
        ))
        XCTAssertEqual(smc.metadata, meta)

        let ms = Question.multipleSelect(MultipleSelectQuestion(
            id: "ms", metadata: meta, prompt: "P", explanation: "E", options: ["A", "B"], answers: [0, 1]
        ))
        XCTAssertEqual(ms.metadata, meta)

        let tf = Question.trueFalse(TrueFalseQuestion(
            id: "tf", metadata: meta, prompt: "P", explanation: "E", answer: true
        ))
        XCTAssertEqual(tf.metadata, meta)

        let match = Question.matching(MatchingQuestion(
            id: "match", metadata: meta, prompt: "P", explanation: "E", leftItems: ["L"], rightItems: ["R"], correctPairs: [0]
        ))
        XCTAssertEqual(match.metadata, meta)
    }

    // MARK: - 2. Nil envelope and empty catalog

    func testNilEnvelopeAndEmptyCatalogYieldAllZeroCountsAndCorrectActivityLength() {
        let insights = StudyInsights.derive(
            envelope: nil,
            catalog: [:],
            now: now,
            calendar: calendar,
            activityDays: 14
        )

        XCTAssertEqual(insights.coverage, StudyCoverage(totalQuestions: 0, seen: 0, answered: 0, correct: 0))
        XCTAssertTrue(insights.areas.isEmpty)
        XCTAssertEqual(insights.due, StudyDueCounts(due: 0, upcoming: 0, unscheduled: 0))
        XCTAssertTrue(insights.recentMisses.isEmpty)
        XCTAssertEqual(insights.activity.count, 14)
        XCTAssertTrue(insights.activity.allSatisfy { $0.answered == 0 && $0.correct == 0 })

        let todayStart = calendar.startOfDay(for: now)
        XCTAssertEqual(insights.activity.last?.day, todayStart)
    }

    func testNilEnvelopeWithCatalogYieldsUnscheduledCountsAndZeroProgress() {
        let q1 = makeQuestion(id: "q1", area: "Domain 1")
        let q2 = makeQuestion(id: "q2", area: "Domain 2")
        let catalog = [
            identity(for: "q1"): q1,
            identity(for: "q2"): q2
        ]

        let insights = StudyInsights.derive(
            envelope: nil,
            catalog: catalog,
            now: now,
            calendar: calendar,
            activityDays: 7
        )

        XCTAssertEqual(insights.coverage, StudyCoverage(totalQuestions: 2, seen: 0, answered: 0, correct: 0))
        XCTAssertEqual(insights.areas.count, 2)
        XCTAssertEqual(insights.due, StudyDueCounts(due: 0, upcoming: 0, unscheduled: 2))
        XCTAssertTrue(insights.recentMisses.isEmpty)
        XCTAssertEqual(insights.activity.count, 7)
    }

    // MARK: - 3. Coverage and per-area rollup joining mastery by identity

    func testCoverageAndPerAreaRollupJoiningMastery() {
        let q1 = makeQuestion(id: "q1", area: "Network Security")
        let q2 = makeQuestion(id: "q2", area: "Network Security")
        let q3 = makeQuestion(id: "q3", area: "Network Security")
        let q4 = makeQuestion(id: "q4", area: "IAM")
        let q5 = makeQuestion(id: "q5", area: "IAM")

        let id1 = identity(for: "q1")
        let id2 = identity(for: "q2")
        let id3 = identity(for: "q3")
        let id4 = identity(for: "q4")
        let id5 = identity(for: "q5")

        let catalog = [
            id1: q1, id2: q2, id3: q3, id4: q4, id5: q5
        ]

        let envelope = ProgressEnvelope(
            actorID: "test-actor",
            mastery: [
                MasterySnapshot(identity: id1, answered: 3, correct: 3),
                MasterySnapshot(identity: id2, answered: 2, correct: 1),
                // id3 unattempted
                MasterySnapshot(identity: id4, answered: 5, correct: 4)
                // id5 unattempted
            ]
        )

        let insights = StudyInsights.derive(
            envelope: envelope,
            catalog: catalog,
            now: now,
            calendar: calendar
        )

        // Coverage: 5 total, 3 seen (q1, q2, q4), answered = 3 + 2 + 5 = 10, correct = 3 + 1 + 4 = 8
        XCTAssertEqual(insights.coverage, StudyCoverage(totalQuestions: 5, seen: 3, answered: 10, correct: 8))

        // Areas: Network Security has 3 questions, 2 seen, 5 answered, 4 correct (accuracy 0.8)
        // IAM has 2 questions, 1 seen, 5 answered, 4 correct (accuracy 0.8)
        let netSec = try! XCTUnwrap(insights.areas.first(where: { $0.area == "Network Security" }))
        XCTAssertEqual(netSec.totalQuestions, 3)
        XCTAssertEqual(netSec.seen, 2)
        XCTAssertEqual(netSec.answered, 5)
        XCTAssertEqual(netSec.correct, 4)
        XCTAssertEqual(netSec.accuracy, 0.8, accuracy: 0.0001)

        let iam = try! XCTUnwrap(insights.areas.first(where: { $0.area == "IAM" }))
        XCTAssertEqual(iam.totalQuestions, 2)
        XCTAssertEqual(iam.seen, 1)
        XCTAssertEqual(iam.answered, 5)
        XCTAssertEqual(iam.correct, 4)
        XCTAssertEqual(iam.accuracy, 0.8, accuracy: 0.0001)
    }

    // MARK: - 4. Off-catalog entries ignored everywhere

    func testOffCatalogEntriesIgnoredEverywhere() throws {
        let installedQ = makeQuestion(id: "installed_1", area: "Core Area")
        let installedID = identity(for: "installed_1")
        let offCatalogID = identity(for: "uninstalled_99", packID: "uninstalled-pack")

        let catalog = [installedID: installedQ]

        let sessionDate = calendar.date(byAdding: .day, value: -1, to: now)!
        let envelope = ProgressEnvelope(
            actorID: "test-actor",
            sessionDetails: [
                SessionDetail(
                    sessionID: "s1",
                    completedAt: sessionDate,
                    answers: [
                        SessionAnswer(identity: offCatalogID, correct: false),
                        SessionAnswer(identity: installedID, correct: true)
                    ]
                )
            ],
            mastery: [
                MasterySnapshot(identity: installedID, answered: 1, correct: 1),
                MasterySnapshot(identity: offCatalogID, answered: 10, correct: 0)
            ],
            srs: [
                SRSSnapshot(identity: offCatalogID, state: try SRSState(tier: 1, nextDueAt: now.addingTimeInterval(-100)))
            ]
        )

        let pending = [
            SessionAnswer(identity: offCatalogID, correct: false)
        ]

        let insights = StudyInsights.derive(
            envelope: envelope,
            catalog: catalog,
            pending: pending,
            now: now,
            calendar: calendar,
            activityDays: 7
        )

        // Coverage reflects only installedID
        XCTAssertEqual(insights.coverage, StudyCoverage(totalQuestions: 1, seen: 1, answered: 1, correct: 1))
        // Areas reflect only Core Area
        XCTAssertEqual(insights.areas.count, 1)
        XCTAssertEqual(insights.areas.first?.area, "Core Area")
        // Due counts reflect installedID (unscheduled), offCatalog ignored
        XCTAssertEqual(insights.due, StudyDueCounts(due: 0, upcoming: 0, unscheduled: 1))
        // Recent misses excludes offCatalogID
        XCTAssertEqual(insights.recentMisses, [])

        // Missed queue excludes offCatalogID
        let missed = StudyInsights.missedQueue(envelope: envelope, catalog: catalog, limit: 10)
        XCTAssertEqual(missed, [])
    }

    // MARK: - 5. Pending answers fold into coverage, areas and today's activity

    func testPendingAnswersFoldIntoCoverageAreasAndTodayActivity() {
        let q1 = makeQuestion(id: "q1", area: "Cloud Security")
        let id1 = identity(for: "q1")
        let catalog = [id1: q1]

        let envelope = ProgressEnvelope(
            actorID: "test-actor",
            mastery: [MasterySnapshot(identity: id1, answered: 1, correct: 1)]
        )

        let pending = [
            SessionAnswer(identity: id1, correct: false),
            SessionAnswer(identity: id1, correct: true)
        ]

        let insights = StudyInsights.derive(
            envelope: envelope,
            catalog: catalog,
            pending: pending,
            now: now,
            calendar: calendar,
            activityDays: 5
        )

        // Coverage: 1 + 2 = 3 answered, 1 + 1 = 2 correct
        XCTAssertEqual(insights.coverage, StudyCoverage(totalQuestions: 1, seen: 1, answered: 3, correct: 2))

        // Areas
        let area = try! XCTUnwrap(insights.areas.first)
        XCTAssertEqual(area.answered, 3)
        XCTAssertEqual(area.correct, 2)
        XCTAssertEqual(area.accuracy, 2.0 / 3.0, accuracy: 0.0001)

        // Activity: today's entry (last) contains pending answers
        let todayDay = try! XCTUnwrap(insights.activity.last)
        XCTAssertEqual(todayDay.day, calendar.startOfDay(for: now))
        XCTAssertEqual(todayDay.answered, 2)
        XCTAssertEqual(todayDay.correct, 1)

        // Pending does not affect due counts
        XCTAssertEqual(insights.due, StudyDueCounts(due: 0, upcoming: 0, unscheduled: 1))
    }

    // MARK: - 6. Area ordering: weakest-first, zero-answered last, deterministic ties

    func testAreaOrderingWeakestFirstZeroAnsweredLastDeterministicTies() {
        // Area A: 2 answered, 0 correct -> accuracy 0.0 (weakest among answered)
        // Area B: 10 answered, 5 correct -> accuracy 0.5
        // Area C: 20 answered, 10 correct -> accuracy 0.5 (same accuracy as B, more answered -> before B)
        // Area D: 10 answered, 5 correct -> accuracy 0.5 (same accuracy and answered as B, "Area B" < "Area D")
        // Area E: 5 answered, 5 correct -> accuracy 1.0 (strongest among answered)
        // Area ZeroZ: 0 answered -> sorts last
        // Area ZeroA: 0 answered -> sorts last, before ZeroZ alphabetically

        let qA = makeQuestion(id: "qA", area: "Area A")
        let qB = makeQuestion(id: "qB", area: "Area B")
        let qC = makeQuestion(id: "qC", area: "Area C")
        let qD = makeQuestion(id: "qD", area: "Area D")
        let qE = makeQuestion(id: "qE", area: "Area E")
        let qZ = makeQuestion(id: "qZ", area: "ZeroZ")
        let q0A = makeQuestion(id: "q0A", area: "ZeroA")

        let idA = identity(for: "qA")
        let idB = identity(for: "qB")
        let idC = identity(for: "qC")
        let idD = identity(for: "qD")
        let idE = identity(for: "qE")
        let idZ = identity(for: "qZ")
        let id0A = identity(for: "q0A")

        let catalog = [
            idA: qA, idB: qB, idC: qC, idD: qD, idE: qE, idZ: qZ, id0A: q0A
        ]

        let envelope = ProgressEnvelope(
            actorID: "test-actor",
            mastery: [
                MasterySnapshot(identity: idA, answered: 2, correct: 0),
                MasterySnapshot(identity: idB, answered: 10, correct: 5),
                MasterySnapshot(identity: idC, answered: 20, correct: 10),
                MasterySnapshot(identity: idD, answered: 10, correct: 5),
                MasterySnapshot(identity: idE, answered: 5, correct: 5)
            ]
        )

        let insights = StudyInsights.derive(
            envelope: envelope,
            catalog: catalog,
            now: now,
            calendar: calendar
        )

        let areaNames = insights.areas.map(\.area)
        XCTAssertEqual(areaNames, [
            "Area A",  // accuracy 0.0
            "Area C",  // accuracy 0.5, answered 20
            "Area B",  // accuracy 0.5, answered 10, "Area B" < "Area D"
            "Area D",  // accuracy 0.5, answered 10
            "Area E",  // accuracy 1.0
            "ZeroA",   // answered 0, "ZeroA" < "ZeroZ"
            "ZeroZ"    // answered 0
        ])
    }

    // MARK: - 7. Due counts and inclusive boundary

    func testDueUpcomingUnscheduledCountsIncludingExactBoundary() throws {
        let qDuePast = makeQuestion(id: "qDuePast")
        let qDueExact = makeQuestion(id: "qDueExact")
        let qUpcoming = makeQuestion(id: "qUpcoming")
        let qUnscheduled = makeQuestion(id: "qUnscheduled")

        let idDuePast = identity(for: "qDuePast")
        let idDueExact = identity(for: "qDueExact")
        let idUpcoming = identity(for: "qUpcoming")
        let idUnscheduled = identity(for: "qUnscheduled")

        let catalog = [
            idDuePast: qDuePast,
            idDueExact: qDueExact,
            idUpcoming: qUpcoming,
            idUnscheduled: qUnscheduled
        ]

        let envelope = ProgressEnvelope(
            actorID: "test-actor",
            srs: [
                SRSSnapshot(identity: idDuePast, state: try SRSState(tier: 1, nextDueAt: now.addingTimeInterval(-60))),
                // nextDueAt exactly equals now -> must count as due
                SRSSnapshot(identity: idDueExact, state: try SRSState(tier: 1, nextDueAt: now)),
                SRSSnapshot(identity: idUpcoming, state: try SRSState(tier: 1, nextDueAt: now.addingTimeInterval(60)))
            ]
        )

        let insights = StudyInsights.derive(
            envelope: envelope,
            catalog: catalog,
            now: now,
            calendar: calendar
        )

        XCTAssertEqual(insights.due.due, 2)
        XCTAssertEqual(insights.due.upcoming, 1)
        XCTAssertEqual(insights.due.unscheduled, 1)
    }

    // MARK: - 8. Recent misses: newest first, latest answer wrong only, capped at 50

    func testRecentMissesTakesOnlyQuestionsWhoseLatestAnswerWasWrong() {
        let q1 = makeQuestion(id: "q1")
        let q2 = makeQuestion(id: "q2")
        let q3 = makeQuestion(id: "q3")
        let q4 = makeQuestion(id: "q4")

        let id1 = identity(for: "q1")
        let id2 = identity(for: "q2")
        let id3 = identity(for: "q3")
        let id4 = identity(for: "q4")

        let catalog = [id1: q1, id2: q2, id3: q3, id4: q4]

        // Session 1 (earlier): q1 wrong, q2 wrong, q3 right
        let session1 = SessionDetail(
            sessionID: "s1",
            completedAt: now.addingTimeInterval(-3600),
            answers: [
                SessionAnswer(identity: id1, correct: false),
                SessionAnswer(identity: id2, correct: false),
                SessionAnswer(identity: id3, correct: true)
            ]
        )

        // Session 2 (later): q1 answered right (remediated!), q2 answered wrong again, q4 answered wrong then right in same session
        let session2 = SessionDetail(
            sessionID: "s2",
            completedAt: now.addingTimeInterval(-1800),
            answers: [
                SessionAnswer(identity: id1, correct: true),
                SessionAnswer(identity: id2, correct: false),
                SessionAnswer(identity: id4, correct: false),
                SessionAnswer(identity: id4, correct: true) // last answer for q4 is correct
            ]
        )

        let envelope = ProgressEnvelope(
            actorID: "test-actor",
            sessionDetails: [session1, session2]
        )

        let insights = StudyInsights.derive(
            envelope: envelope,
            catalog: catalog,
            now: now,
            calendar: calendar
        )

        // Only q2 has its most recent recorded answer as incorrect.
        // q1: most recent answer was in session2 (correct)
        // q3: most recent answer was in session1 (correct)
        // q4: most recent answer was in session2 (correct)
        XCTAssertEqual(insights.recentMisses, [id2])
    }

    func testRecentMissesDeterministicTieBreakingAndCapAt50() {
        var catalog: [QuestionIdentity: Question] = [:]
        var answers: [SessionAnswer] = []

        // 60 questions missed in a single session
        for i in 0..<60 {
            let idString = String(format: "q%02d", i)
            let q = makeQuestion(id: idString)
            let qid = identity(for: idString)
            catalog[qid] = q
            answers.append(SessionAnswer(identity: qid, correct: false))
        }

        let session = SessionDetail(sessionID: "s-all-misses", completedAt: now, answers: answers)
        let envelope = ProgressEnvelope(actorID: "test-actor", sessionDetails: [session])

        let insights = StudyInsights.derive(
            envelope: envelope,
            catalog: catalog,
            now: now,
            calendar: calendar
        )

        XCTAssertEqual(insights.recentMisses.count, 50)
        // Answers are walked in reverse order, so q59 is first, q10 is 50th
        XCTAssertEqual(insights.recentMisses.first, identity(for: "q59"))
        XCTAssertEqual(insights.recentMisses.last, identity(for: "q10"))
    }

    // MARK: - 9. missedQueue: first tier, mastery top-up, and limit cap

    func testMissedQueueTwoTierTopUpAndLimitCap() {
        // Questions setup:
        // q_recent: latest answer wrong in sessionDetails
        // q_remediated: recent answer right, but has historical misses in mastery
        // q_historical_high: no recent session, 10 answered, 2 correct -> 8 misses
        // q_historical_mid_b: no recent session, 6 answered, 2 correct -> 4 misses ("cissp::core::b")
        // q_historical_mid_a: no recent session, 6 answered, 2 correct -> 4 misses ("cissp::core::a")
        // q_perfect: 5 answered, 5 correct -> 0 misses (excluded)
        // q_unattempted: 0 answered, 0 correct -> 0 misses (excluded)

        let qRecent = makeQuestion(id: "recent")
        let qRemediated = makeQuestion(id: "remediated")
        let qHigh = makeQuestion(id: "high")
        let qMidB = makeQuestion(id: "b")
        let qMidA = makeQuestion(id: "a")
        let qPerfect = makeQuestion(id: "perfect")
        let qUnattempted = makeQuestion(id: "unattempted")

        let idRecent = identity(for: "recent")
        let idRemediated = identity(for: "remediated")
        let idHigh = identity(for: "high")
        let idMidB = identity(for: "b")
        let idMidA = identity(for: "a")
        let idPerfect = identity(for: "perfect")
        let idUnattempted = identity(for: "unattempted")

        let catalog = [
            idRecent: qRecent,
            idRemediated: qRemediated,
            idHigh: qHigh,
            idMidB: qMidB,
            idMidA: qMidA,
            idPerfect: qPerfect,
            idUnattempted: qUnattempted
        ]

        let session = SessionDetail(
            sessionID: "s1",
            completedAt: now,
            answers: [
                SessionAnswer(identity: idRecent, correct: false),
                SessionAnswer(identity: idRemediated, correct: true)
            ]
        )

        let envelope = ProgressEnvelope(
            actorID: "test-actor",
            sessionDetails: [session],
            mastery: [
                MasterySnapshot(identity: idRecent, answered: 3, correct: 1), // also in mastery with misses, but should not duplicate
                MasterySnapshot(identity: idRemediated, answered: 5, correct: 3), // 2 misses
                MasterySnapshot(identity: idHigh, answered: 10, correct: 2), // 8 misses
                MasterySnapshot(identity: idMidB, answered: 6, correct: 2), // 4 misses
                MasterySnapshot(identity: idMidA, answered: 6, correct: 2), // 4 misses
                MasterySnapshot(identity: idPerfect, answered: 5, correct: 5), // 0 misses
                MasterySnapshot(identity: idUnattempted, answered: 0, correct: 0) // 0 misses
            ]
        )

        // Limit <= 0 returns empty
        XCTAssertEqual(StudyInsights.missedQueue(envelope: envelope, catalog: catalog, limit: 0), [])
        XCTAssertEqual(StudyInsights.missedQueue(envelope: envelope, catalog: catalog, limit: -5), [])

        // Limit 1 takes only the first tier (recent)
        XCTAssertEqual(StudyInsights.missedQueue(envelope: envelope, catalog: catalog, limit: 1), [idRecent])

        // Limit 4 takes tier 1 (idRecent), then tops up from mastery:
        // Candidates in mastery:
        // idHigh (8 misses)
        // idMidA (4 misses, "a" < "b")
        // idMidB (4 misses)
        // idRemediated (2 misses)
        // Need 3 top-ups: idHigh, idMidA, idMidB
        let queue4 = StudyInsights.missedQueue(envelope: envelope, catalog: catalog, limit: 4)
        XCTAssertEqual(queue4, [idRecent, idHigh, idMidA, idMidB])

        // Limit 10 (exceeds total available missed questions):
        // Returns all 5 distinct missed questions, never exceeding available
        let queue10 = StudyInsights.missedQueue(envelope: envelope, catalog: catalog, limit: 10)
        XCTAssertEqual(queue10, [idRecent, idHigh, idMidA, idMidB, idRemediated])
    }

    // MARK: - 10. Activity strip length, ordering, and windowing

    func testActivityStripLengthOrderingAndWindowing() {
        let q1 = makeQuestion(id: "q1")
        let id1 = identity(for: "q1")
        let catalog = [id1: q1]

        let today = calendar.startOfDay(for: now)
        let oneDayAgo = calendar.date(byAdding: .day, value: -1, to: today)!
        let threeDaysAgo = calendar.date(byAdding: .day, value: -3, to: today)!
        let tenDaysAgo = calendar.date(byAdding: .day, value: -10, to: today)! // outside 7-day window

        let sToday = SessionDetail(
            sessionID: "s-today",
            completedAt: now,
            answers: [SessionAnswer(identity: id1, correct: true)]
        )
        let sOneDayAgo = SessionDetail(
            sessionID: "s-1d",
            completedAt: oneDayAgo.addingTimeInterval(3600),
            answers: [
                SessionAnswer(identity: id1, correct: false),
                SessionAnswer(identity: id1, correct: true)
            ]
        )
        let sThreeDaysAgo = SessionDetail(
            sessionID: "s-3d",
            completedAt: threeDaysAgo.addingTimeInterval(7200),
            answers: [SessionAnswer(identity: id1, correct: false)]
        )
        let sTenDaysAgo = SessionDetail(
            sessionID: "s-10d",
            completedAt: tenDaysAgo,
            answers: [SessionAnswer(identity: id1, correct: true)]
        )

        let envelope = ProgressEnvelope(
            actorID: "test-actor",
            sessionDetails: [sToday, sOneDayAgo, sThreeDaysAgo, sTenDaysAgo]
        )

        let pending = [SessionAnswer(identity: id1, correct: true)]

        let insights = StudyInsights.derive(
            envelope: envelope,
            catalog: catalog,
            pending: pending,
            now: now,
            calendar: calendar,
            activityDays: 7
        )

        // Exactly 7 entries, oldest -> newest
        XCTAssertEqual(insights.activity.count, 7)
        let days = insights.activity.map(\.day)
        let expectedDays = (0..<7).map { i in
            calendar.startOfDay(for: calendar.date(byAdding: .day, value: i - 6, to: today)!)
        }
        XCTAssertEqual(days, expectedDays)

        // Index 6 (today): 1 answer from sToday + 1 pending answer = 2 answered, 2 correct
        XCTAssertEqual(insights.activity[6].answered, 2)
        XCTAssertEqual(insights.activity[6].correct, 2)

        // Index 5 (-1 day): 2 answered, 1 correct
        XCTAssertEqual(insights.activity[5].answered, 2)
        XCTAssertEqual(insights.activity[5].correct, 1)

        // Index 4 (-2 days): 0 answered, 0 correct
        XCTAssertEqual(insights.activity[4].answered, 0)
        XCTAssertEqual(insights.activity[4].correct, 0)

        // Index 3 (-3 days): 1 answered, 0 correct
        XCTAssertEqual(insights.activity[3].answered, 1)
        XCTAssertEqual(insights.activity[3].correct, 0)

        // Indices 0, 1, 2: 0 answered, 0 correct (10-day-ago session excluded)
        XCTAssertEqual(insights.activity[0].answered, 0)
        XCTAssertEqual(insights.activity[1].answered, 0)
        XCTAssertEqual(insights.activity[2].answered, 0)
    }
}
