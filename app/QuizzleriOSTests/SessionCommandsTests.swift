import XCTest
@testable import QuizzleriOS

/// C4 + Task 17: the Mac track's headless half — the tab bar policy table,
/// the Session menu's availability matrix, and the structural guarantee that
/// the gate scripts never pick up the Mac Catalyst UI tests.
final class SessionCommandsTests: XCTestCase {
    private var appRoot: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    }

    // MARK: - Tab bar policy (Task 17)

    func testTabBarPolicyShowsTheBarOnlyOutsideTheStudyFlow() {
        XCTAssertTrue(CatalystTabBarPolicy.isVisible(for: .today), "Today must show the Mac tab bar")
        XCTAssertTrue(CatalystTabBarPolicy.isVisible(for: .progress), "Progress must show the Mac tab bar")
        XCTAssertTrue(CatalystTabBarPolicy.isVisible(for: .settings), "Settings must show the Mac tab bar")
        XCTAssertFalse(CatalystTabBarPolicy.isVisible(for: .question), "the tab bar must hide during a question")
        XCTAssertFalse(CatalystTabBarPolicy.isVisible(for: .feedback), "the tab bar must hide during feedback")
        XCTAssertFalse(CatalystTabBarPolicy.isVisible(for: .results), "the tab bar must hide on the summary")
    }

    func testTabBarPolicyExhaustsEveryLaunchpadState() {
        // A new LaunchpadState case must fail here until its visibility is decided.
        let decided: [LaunchpadState: Bool] = [
            .today: true,
            .progress: true,
            .settings: true,
            .question: false,
            .feedback: false,
            .results: false
        ]
        for state in LaunchpadState.allCases {
            guard let expected = decided[state] else {
                XCTFail("CatalystTabBarPolicy has no decided visibility for \(state)")
                continue
            }
            XCTAssertEqual(
                CatalystTabBarPolicy.isVisible(for: state),
                expected,
                "unexpected Mac tab bar visibility for \(state)"
            )
        }
    }

    // MARK: - Session command availability (C4)

    func testPrimaryCommandWaitsForASelection() {
        let unanswered = SessionCommandAvailability(
            state: .question, isFeedback: false, hasSelection: false, isLastQuestion: false
        )
        XCTAssertFalse(unanswered.primary, "Check Answer must stay disabled until an answer is selected")
        XCTAssertTrue(unanswered.skip, "an unanswered question must be skippable")
        XCTAssertTrue(unanswered.end, "a session question must offer Back to Today")

        let answered = SessionCommandAvailability(
            state: .question, isFeedback: false, hasSelection: true, isLastQuestion: false
        )
        XCTAssertTrue(answered.primary, "a selection must enable the primary command")
        XCTAssertTrue(answered.skip, "an unanswered question must stay skippable")
        XCTAssertTrue(answered.end, "Back to Today must stay available while answering")
    }

    func testFeedbackEnablesPrimaryAndHidesSkip() {
        let feedback = SessionCommandAvailability(
            state: .feedback, isFeedback: true, hasSelection: true, isLastQuestion: false
        )
        XCTAssertTrue(feedback.primary, "feedback must offer Next question")
        XCTAssertFalse(feedback.skip, "skip is hidden in feedback")
        XCTAssertTrue(feedback.end, "feedback must offer Back to Today")
    }

    func testCommandsAreUnavailableOutsideASession() {
        for state in [LaunchpadState.today, .progress, .results, .settings] {
            let availability = SessionCommandAvailability(
                state: state, isFeedback: false, hasSelection: true, isLastQuestion: false
            )
            XCTAssertFalse(availability.primary, "\(state) must not offer a primary command")
            XCTAssertFalse(availability.skip, "\(state) must not offer skip")
            XCTAssertFalse(availability.end, "\(state) must not offer Back to Today")
        }
    }

    func testPrimaryTitleNamesCheckAnswerNextAndFinish() {
        let answering = SessionCommandAvailability(
            state: .question, isFeedback: false, hasSelection: true, isLastQuestion: false
        )
        XCTAssertEqual(answering.primaryTitle, "Check Answer")

        let advancing = SessionCommandAvailability(
            state: .feedback, isFeedback: true, hasSelection: true, isLastQuestion: false
        )
        XCTAssertEqual(advancing.primaryTitle, "Next question")

        let finishing = SessionCommandAvailability(
            state: .feedback, isFeedback: true, hasSelection: true, isLastQuestion: true
        )
        XCTAssertEqual(finishing.primaryTitle, "Finish session")
    }

    // MARK: - Gate isolation (Task 17)

    /// The Mac Catalyst UI tests run only at a milestone, via
    /// `scripts/mac_milestone_ui_tests.sh`. If the gate ever starts naming
    /// their class, the simulator gates stop being hermetic — fail here.
    func testGateDoesNotNameTheMacCatalystUITests() throws {
        let gate = try String(contentsOf: appRoot.appendingPathComponent("test-gate.sh"), encoding: .utf8)
        XCTAssertFalse(
            gate.contains("MacCatalystUITests"),
            "test-gate.sh must not run the Mac Catalyst UI tests; they run only at a milestone via scripts/mac_milestone_ui_tests.sh"
        )
    }
}
