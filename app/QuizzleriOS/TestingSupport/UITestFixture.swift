import SwiftUI

#if DEBUG

/// A deterministic, offline-only surface used by the UI test target.
///
/// The file name and the Release source exclusion are intentional: this
/// fixture must never be present in an archived app or a release pack.
enum UITestFixture {
    static let environmentKey = "QUIZZLER_UI_TEST_FIXTURE"
    static let enabledValue = "enabled"

    static var isEnabled: Bool {
        ProcessInfo.processInfo.environment[environmentKey] == enabledValue
    }
}

struct UITestFixtureView: View {
    private enum Screen: Equatable {
        case launch
        case pack
        case mode
        case question(Int)
        case feedback
        case report
        case sync
        case complete
    }

    private struct QuestionFixture: Identifiable {
        let id: String
        let type: String
        let prompt: String
        let answer: String
    }

    private static let questions = [
        QuestionFixture(id: "q1", type: "Single choice", prompt: "Choose one control.", answer: "Network segmentation"),
        QuestionFixture(id: "q2", type: "Scenario single choice", prompt: "Choose the phishing-resistant factor.", answer: "Hardware security key"),
        QuestionFixture(id: "q3", type: "Select all", prompt: "Choose both protective practices.", answer: "Least privilege"),
        QuestionFixture(id: "q4", type: "True or false", prompt: "Encryption limits lateral movement.", answer: "False"),
        QuestionFixture(id: "q5", type: "Matching", prompt: "Match each control to its goal.", answer: "Segmentation"),
    ]

    @State private var screen: Screen = .launch
    @State private var questionIndex = 0
    @State private var attempts = 0
    @State private var syncState = "Synced"

    var body: some View {
        VStack(spacing: 0) {
            Text("Quizzler UI fixture")
                .font(.headline)
                .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                .accessibilityIdentifier("fixture-title")
            content
        }
        .padding(20)
        .background(Color.black.ignoresSafeArea())
        .foregroundStyle(.white)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("fixture-root")
    }

    @ViewBuilder private var content: some View {
        switch screen {
        case .launch:
            VStack(alignment: .leading, spacing: 16) {
                Text("Today")
                    .font(.largeTitle)
                    .accessibilityAddTraits(.isHeader)
                Text("Deterministic offline study fixture")
                fixtureButton("Select pack", identifier: "fixture-select-pack") { screen = .pack }
                fixtureButton("Select mode", identifier: "fixture-select-mode") { screen = .mode }
                fixtureButton("Start review", identifier: "fixture-start-review") { screen = .question(0) }
                fixtureButton("Sync state", identifier: "fixture-sync-state") { screen = .sync }
            }
        case .pack:
            selectionScreen(title: "Select pack", value: "Security+", identifier: "fixture-pack") {
                screen = .launch
            }
        case .mode:
            selectionScreen(title: "Select mode", value: "Normal review", identifier: "fixture-mode") {
                screen = .launch
            }
        case .question(let index):
            let question = Self.questions[index]
            VStack(alignment: .leading, spacing: 14) {
                Text("Question")
                    .font(.title)
                    .accessibilityAddTraits(.isHeader)
                Text(question.type)
                    .accessibilityIdentifier("fixture-question-type")
                Text(question.prompt)
                    .accessibilityIdentifier("fixture-question-prompt")
                Text("Question ID sy0-701::\(question.id)")
                    .accessibilityIdentifier("fixture-question-id")
                fixtureButton(question.answer, identifier: "fixture-answer") {
                    attempts += 1
                    screen = .feedback
                }
                fixtureButton("Report", identifier: "fixture-report") { screen = .report }
                fixtureButton("Pending sync", identifier: "fixture-pending-sync") {
                    syncState = "Pending sync"
                    screen = .sync
                }
                fixtureButton("Conflict", identifier: "fixture-conflict") {
                    syncState = "Conflict detected"
                    screen = .sync
                }
            }
        case .feedback:
            VStack(alignment: .leading, spacing: 14) {
                Text("Feedback")
                    .font(.title)
                    .accessibilityAddTraits(.isHeader)
                Text("Correct")
                    .accessibilityIdentifier("fixture-feedback")
                Text("Attempts recorded: \(attempts)")
                    .accessibilityIdentifier("fixture-attempt-count")
                fixtureButton("Retry missed", identifier: "fixture-retry") { screen = .question(questionIndex) }
                fixtureButton(questionIndex + 1 == Self.questions.count ? "Finish Session" : "Next question", identifier: "fixture-next") {
                    if questionIndex + 1 == Self.questions.count {
                        screen = .complete
                    } else {
                        questionIndex += 1
                        screen = .question(questionIndex)
                    }
                }
            }
        case .report:
            VStack(alignment: .leading, spacing: 14) {
                Text("Report question")
                    .font(.title)
                    .accessibilityAddTraits(.isHeader)
                Text("Preview")
                    .accessibilityIdentifier("fixture-report-preview")
                Text("Reports include question context only.")
                fixtureButton("Queue report", identifier: "fixture-queue-report") { screen = .question(questionIndex) }
                fixtureButton("Cancel", identifier: "fixture-cancel-report") { screen = .question(questionIndex) }
            }
        case .sync:
            VStack(alignment: .leading, spacing: 14) {
                Text("Sync recovery")
                    .font(.title)
                    .accessibilityAddTraits(.isHeader)
                Text(syncState)
                    .accessibilityIdentifier("fixture-sync-status")
                fixtureButton("Retry", identifier: "fixture-sync-retry") {
                    syncState = "Synced"
                    screen = .launch
                }
                fixtureButton("Recover offline", identifier: "fixture-offline-recovery") {
                    syncState = "Offline recovery ready"
                }
            }
        case .complete:
            VStack(alignment: .leading, spacing: 14) {
                Text("Results")
                    .font(.title)
                    .accessibilityAddTraits(.isHeader)
                Text("Session complete")
                    .accessibilityIdentifier("fixture-completion")
                fixtureButton("Continue review", identifier: "fixture-continue") {
                    questionIndex = 0
                    screen = .question(0)
                }
            }
        }
    }

    private func selectionScreen(title: String, value: String, identifier: String, onContinue: @escaping () -> Void) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(title)
                .font(.title)
                .accessibilityAddTraits(.isHeader)
            Button(action: onContinue) {
                Text(value)
                    .frame(maxWidth: .infinity, minHeight: 48, alignment: .leading)
                    .contentShape(Rectangle())
            }
                .accessibilityIdentifier(identifier)
            fixtureButton("Continue", identifier: "fixture-selection-continue", action: onContinue)
        }
    }

    private func fixtureButton(_ title: String, identifier: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .frame(maxWidth: .infinity, minHeight: 48)
                .contentShape(Rectangle())
        }
            .accessibilityIdentifier(identifier)
    }
}

#endif
