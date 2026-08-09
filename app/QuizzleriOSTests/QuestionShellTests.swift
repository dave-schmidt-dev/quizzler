import XCTest
@testable import QuizzleriOS
import QuizzlerKit

final class QuestionShellTests: XCTestCase {
    func testLaunchpadHasSixApprovedStates() {
        XCTAssertEqual(Set(LaunchpadState.allCases), Set([.today, .question, .feedback, .results, .progress, .settings]))
    }

    func testSeededDataCoversEveryRenderer() {
#if DEBUG
        XCTAssertEqual(Set(SeededStudyData.questions.map { $0.question.type }), Set(QuestionType.allCases))
#else
        XCTAssertEqual(Set(SeededStudyData.questions.map { $0.question.type }), Set([.multipleChoice, .scenarioMultipleChoice, .multipleSelect]))
#endif
    }

    func testQuestionIdentityAndReportRemainAvailableForFeedback() {
        let question = SeededStudyData.today
        XCTAssertFalse(question.qid.isEmpty)
        let context = ReportQuestionContext(identity: question.identity, qid: question.qid, type: question.question.type.rawValue, course: question.courseTitle, appVersion: "1.0.0", selectedResponse: "Network segmentation")
        XCTAssertEqual(context.qid, question.qid)
        XCTAssertEqual(context.identity, question.identity)
    }

    func testSelectionCorrectnessContracts() {
        let multipleChoice = SeededStudyData.questions[0].question
        XCTAssertEqual(QuestionShellView.correctAnswer(for: multipleChoice, selection: .single(0)), true)
        XCTAssertEqual(QuestionShellView.correctAnswer(for: multipleChoice, selection: .single(1)), false)
        let multipleSelect = SeededStudyData.questions[2].question
        XCTAssertEqual(QuestionShellView.correctAnswer(for: multipleSelect, selection: .multiple([0, 1])), true)
    }

    func testSelectionCorrectnessCoversEveryQuestionType() {
        let scenario = SeededStudyData.questions[1].question
        XCTAssertTrue(QuestionShellView.correctAnswer(for: scenario, selection: .single(0)))

        let trueFalse = SeededStudyData.questions[3].question
        XCTAssertTrue(QuestionShellView.correctAnswer(for: trueFalse, selection: .boolean(false)))

        let matching = SeededStudyData.questions[4].question
        XCTAssertTrue(QuestionShellView.correctAnswer(for: matching, selection: .matching([0, 1, 2])))
        XCTAssertFalse(QuestionShellView.correctAnswer(for: matching, selection: .matching([-1, 1, 2])))
    }

    func testIncompleteMatchingSelectionRemainsEmptyUntilEveryPairIsChosen() {
        XCTAssertTrue(QuestionSelection.matching([-1, 1, 2]).isEmpty)
        XCTAssertFalse(QuestionSelection.matching([0, 1, 2]).isEmpty)
    }
}
