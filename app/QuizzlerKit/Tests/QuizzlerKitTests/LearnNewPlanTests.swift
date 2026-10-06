import Foundation
import XCTest
@testable import QuizzlerKit

/// "Learn new" builds a `.normal` plan that skips every identity the learner
/// has already answered, while keeping the pack-order start and wrap behavior
/// the pack-order path uses.
final class LearnNewPlanTests: XCTestCase {
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

    private func buildPlan(
        packOrder: [QuestionIdentity],
        resumeIndex: Int,
        excluding: Set<QuestionIdentity>,
        limit: Int
    ) throws -> StudySessionPlan {
        var catalog: [QuestionIdentity: Question] = [:]
        for id in packOrder {
            catalog[id] = makeQuestion(id: id.questionID)
        }
        let request = try SelectionRequest(mode: .normal, limit: limit)
        return StudySessionPlan.build(
            request: request,
            envelope: nil,
            catalog: catalog,
            packOrder: packOrder,
            resumeIndex: resumeIndex,
            excluding: excluding,
            now: now
        )
    }

    // MARK: - 1. Seen questions are skipped; pack order and wrap preserved

    func testLearnNewSkipsSeenQuestionsAndWrapsPackOrder() throws {
        let ids = ["A", "B", "C", "D", "E"].map { identity(for: $0) }
        // resumeIndex 1 starts at B. B and D are seen, so the plan serves the
        // unseen questions in pack order, wrapping past E back to A.
        let plan = try buildPlan(packOrder: ids, resumeIndex: 1, excluding: [ids[1], ids[3]], limit: 10)
        XCTAssertEqual(plan.mode, .normal)
        XCTAssertEqual(plan.questions, [ids[2], ids[4], ids[0]])
    }

    // MARK: - 2. A question seen through another mode is not served

    func testNextPackOrderQuestionSeenThroughAnotherModeIsNotServed() throws {
        let ids = ["A", "B", "C", "D", "E"].map { identity(for: $0) }
        // Without exclusion the next pack-order question is A.
        let baseline = try buildPlan(packOrder: ids, resumeIndex: 0, excluding: [], limit: 10)
        XCTAssertEqual(baseline.questions.first, ids[0])

        // A was answered through another mode (a due review, say), so Learn
        // new must skip it and start at B instead.
        let plan = try buildPlan(packOrder: ids, resumeIndex: 0, excluding: [ids[0]], limit: 10)
        XCTAssertFalse(plan.questions.contains(ids[0]))
        XCTAssertEqual(plan.questions.first, ids[1])
    }

    // MARK: - 3. Every question seen yields an empty plan

    func testAllQuestionsSeenYieldsEmptyPlan() throws {
        let ids = ["A", "B", "C", "D", "E"].map { identity(for: $0) }
        let plan = try buildPlan(packOrder: ids, resumeIndex: 1, excluding: Set(ids), limit: 10)
        XCTAssertTrue(plan.questions.isEmpty)
    }

    // MARK: - 4. The limit caps at the unseen count

    func testLimitIsCappedToUnseenCount() throws {
        let ids = ["A", "B", "C", "D", "E"].map { identity(for: $0) }
        let seen: Set<QuestionIdentity> = [ids[1], ids[3]]

        // A limit larger than the three unseen questions serves exactly three.
        let planLarge = try buildPlan(packOrder: ids, resumeIndex: 1, excluding: seen, limit: 10)
        XCTAssertEqual(planLarge.questions.count, 3)
        XCTAssertEqual(planLarge.questions, [ids[2], ids[4], ids[0]])

        // A limit smaller than the unseen count still respects the limit.
        let planSmall = try buildPlan(packOrder: ids, resumeIndex: 1, excluding: seen, limit: 2)
        XCTAssertEqual(planSmall.questions, [ids[2], ids[4]])
    }
}
