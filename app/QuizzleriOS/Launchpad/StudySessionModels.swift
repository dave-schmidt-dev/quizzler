import Foundation
import SwiftUI
import QuizzlerKit
import UIKit

enum LeitnerSchedule {
    static let intervalDays = [1, 3, 7, 14, 30, 60, 120]
    static let defaultMaximumLevel = 5

    static func intervalDays(for level: Int) -> Int? {
        guard intervalDays.indices.contains(level - 1) else { return nil }
        return intervalDays[level - 1]
    }

    static func intervalLabel(for level: Int) -> String {
        guard let days = intervalDays(for: level) else { return "Unknown interval" }
        return "\(days) \(days == 1 ? "day" : "days")"
    }

    static func nextLevel(current: Int?, correct: Bool, maximum: Int) -> Int {
        let prior = min(maximum, current ?? 1)
        return correct ? min(maximum, prior + 1) : max(1, prior - 2)
    }
}

/// The shared aggregate belongs to every signed-in device. Resume position is
/// deliberately local to this device and selected pack, so answers completed
/// elsewhere can improve visible mastery without skipping this review queue.
/// How many questions a normal session serves.
///
/// Sessions were fixed at ten. The length is a study decision, not a build
/// constant, so it lives in Settings; `wholePack` is a sentinel rather than a
/// number because the pack size changes with the selected course.
enum StudySessionLength {
    static let key = "quizzler.session-length.v1"

    /// The sentinel stored for "every question in the pack".
    static let wholePack = 0

    /// Offered in Settings, in the order they appear there.
    static let options = [10, 20, 40, wholePack]

    static let `default` = 10

    static func label(_ value: Int) -> String {
        value == wholePack ? "Whole pack" : "\(value) questions"
    }

    /// The Today selection is intentionally temporary. It can refine the next
    /// session without rewriting the learner's Settings default.
    static func effective(stored: Int, nextSessionOverride: Int?) -> Int {
        if let nextSessionOverride, options.contains(nextSessionOverride) {
            return nextSessionOverride
        }
        return options.contains(stored) ? stored : `default`
    }

    static func maximumLabel(_ value: Int) -> String {
        value == wholePack ? "Whole pack" : "Up to \(value) questions"
    }

    /// The number of questions to request. A stored value that is not one of
    /// the offered options falls back to the default rather than trusting it,
    /// so a corrupted or hand-edited default cannot request a nonsense limit.
    static func limit(stored: Int, packQuestionCount: Int) -> Int {
        guard packQuestionCount > 0 else { return 0 }
        guard options.contains(stored) else { return min(`default`, packQuestionCount) }
        if stored == wholePack { return packQuestionCount }
        return min(stored, packQuestionCount)
    }

    static func limit(stored: Int, nextSessionOverride: Int?, candidateCount: Int) -> Int {
        limit(
            stored: effective(stored: stored, nextSessionOverride: nextSessionOverride),
            packQuestionCount: candidateCount
        )
    }
}

enum StudyScheduledReview {
    static let key = "quizzler.scheduled-review.v1"
    static let `default` = true
}

enum NativeAppVersion {
    static var display: String {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String
        switch (version?.trimmingCharacters(in: .whitespacesAndNewlines), build?.trimmingCharacters(in: .whitespacesAndNewlines)) {
        case let (.some(v), .some(b)) where !v.isEmpty && !b.isEmpty:
            return "\(v) (\(b))"
        case let (.some(v), _) where !v.isEmpty:
            return v
        case let (_, .some(b)) where !b.isEmpty:
            return "(\(b))"
        default:
            return "Unavailable"
        }
    }
}

enum StudyResumePosition {
    private static let keyPrefix = "quizzler.study-resume-position.v1"

    static func index(
        courseID: String,
        packID: String,
        questionCount: Int,
        defaults: UserDefaults = .standard
    ) -> Int {
        guard questionCount > 0 else { return 0 }
        let value = defaults.object(forKey: key(courseID: courseID, packID: packID)) as? Int ?? 0
        return ((value % questionCount) + questionCount) % questionCount
    }

    static func store(
        _ index: Int,
        courseID: String,
        packID: String,
        questionCount: Int,
        defaults: UserDefaults = .standard
    ) {
        guard questionCount > 0 else { return }
        defaults.set(
            ((index % questionCount) + questionCount) % questionCount,
            forKey: key(courseID: courseID, packID: packID)
        )
    }

    private static func key(courseID: String, packID: String) -> String {
        "\(keyPrefix).\(courseID).\(packID)"
    }
}

/// Determines the primary study action and time estimate on the Today screen.
enum TodayRecommendation: Equatable, Sendable {
    case review(batch: Int, due: Int)
    case learn(batch: Int, unseen: Int)
    case caughtUp(batch: Int)

    init(due: Int, unseen: Int, sessionLimit: Int, scheduledReviewEnabled: Bool = true) {
        if scheduledReviewEnabled, due > 0 {
            self = .review(batch: min(due, sessionLimit), due: due)
        } else if unseen > 0 {
            self = .learn(batch: min(unseen, sessionLimit), unseen: unseen)
        } else {
            self = .caughtUp(batch: sessionLimit)
        }
    }

    var batch: Int {
        switch self {
        case .review(let batch, _): batch
        case .learn(let batch, _): batch
        case .caughtUp(let batch): batch
        }
    }

    var minutes: Int {
        max(1, Int(ceil(Double(batch) * 0.75)))
    }

    var title: String {
        switch self {
        case .review(let batch, _):
            "Scheduled review: \(batch) \(batch == 1 ? "question" : "questions")"
        case .learn:
            "Ready to learn"
        case .caughtUp:
            "Ready to practice"
        }
    }

    var detail: String {
        let minuteWord = minutes == 1 ? "minute" : "minutes"
        switch self {
        case .review(let batch, let due):
            let backlog = due > batch ? " · \(due) due overall" : ""
            return "Spaced repetition\(backlog) · about \(minutes) \(minuteWord)"
        case .learn(let batch, _):
            let questionWord = batch == 1 ? "question" : "questions"
            return "Learn \(batch) new \(questionWord) · about \(minutes) \(minuteWord)"
        case .caughtUp:
            return "About \(minutes) \(minuteWord)"
        }
    }

    var buttonTitle: String {
        switch self {
        case .review:
            "Start review"
        case .learn:
            "Start learning"
        case .caughtUp:
            "Keep practicing"
        }
    }

    var isReview: Bool {
        if case .review = self { return true }
        return false
    }

    var isLearn: Bool {
        if case .learn = self { return true }
        return false
    }

    var isCaughtUp: Bool {
        if case .caughtUp = self { return true }
        return false
    }
}

/// The questions this session will serve, and how far through them we are.
/// `nil` between sessions, when the position follows from saved progress instead.
/// It is pinned for the duration of a session so recording an answer cannot
/// swap the question out from under the Feedback screen.
struct ActiveSession {
    let mode: SelectionMode
    let questions: [StudyQuestion]
    var position: Int
    var answers: [SessionAnswer]
    let newIdentities: Set<QuestionIdentity>

    init(
        mode: SelectionMode,
        questions: [StudyQuestion],
        position: Int = 0,
        answers: [SessionAnswer] = [],
        newIdentities: Set<QuestionIdentity> = []
    ) {
        self.mode = mode
        self.questions = questions
        self.position = position
        self.answers = answers
        self.newIdentities = newIdentities
    }

    /// Returns a copy with `position` incremented by one, or `nil` when the
    /// session has no remaining questions. Used by finish and skip so both
    /// paths advance the position through the same expression.
    func advanced() -> ActiveSession? {
        let next = position + 1
        guard next < questions.count else { return nil }
        var copy = self
        copy.position = next
        return copy
    }
}
