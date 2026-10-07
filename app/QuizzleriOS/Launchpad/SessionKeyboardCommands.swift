import SwiftUI

/// Which Session menu commands are enabled, derived as a pure value from the
/// launchpad state so the matrix can be unit-tested without a scene (C4).
struct SessionCommandAvailability: Equatable {
    let primary: Bool
    let skip: Bool
    let end: Bool
    private let isFeedback: Bool
    private let isLastQuestion: Bool

    init(state: LaunchpadState, isFeedback: Bool, hasSelection: Bool, isLastQuestion: Bool) {
        self.isFeedback = isFeedback
        self.isLastQuestion = isLastQuestion
        let inSession = state == .question || state == .feedback
        // Check Answer waits for a selection; feedback's Next is always ready.
        primary = inSession && (isFeedback || hasSelection)
        // Skip moves past an unanswered question; feedback has nothing to skip.
        skip = inSession && !isFeedback
        // Back to Today exists only while a session is running; the summary
        // ends through its own buttons.
        end = inSession
    }

    /// The primary command's menu title, naming the same action the pinned
    /// bottom bar's primary button shows. On the session's last question the
    /// feedback title names where it goes (C6).
    var primaryTitle: String {
        if isFeedback {
            return isLastQuestion ? "Finish session" : "Next question"
        }
        return "Check Answer"
    }
}

/// The session actions the Session menu invokes, published by LaunchpadView
/// through the focused scene and consumed by `SessionCommands`.
struct SessionCommandsValue {
    let availability: SessionCommandAvailability
    let primary: () -> Void
    let skip: () -> Void
    let end: () -> Void
}

struct SessionCommandsKey: FocusedValueKey {
    typealias Value = SessionCommandsValue
}

extension FocusedValues {
    var sessionCommands: SessionCommandsValue? {
        get { self[SessionCommandsKey.self] }
        set { self[SessionCommandsKey.self] = newValue }
    }
}

/// The Mac's Session menu (C4): Return runs the primary action — Check Answer
/// while answering, Next question (or Finish session) in feedback — S skips,
/// and Escape returns to Today. The menu follows the focused scene's session
/// through the `\.sessionCommands` focused value. The menu deliberately does
/// not list the 1-9 answer keys; those stay on the question's own rows.
struct SessionCommands: Commands {
    @FocusedValue(\.sessionCommands) private var sessionCommands

    var body: some Commands {
        CommandMenu("Session") {
            Button(sessionCommands?.availability.primaryTitle ?? "Check Answer") {
                sessionCommands?.primary()
            }
            .disabled(sessionCommands?.availability.primary != true)

            Button("Skip") {
                sessionCommands?.skip()
            }
            .disabled(sessionCommands?.availability.skip != true)

            Button("Back to Today") {
                sessionCommands?.end()
            }
            .disabled(sessionCommands?.availability.end != true)
        }
    }
}
