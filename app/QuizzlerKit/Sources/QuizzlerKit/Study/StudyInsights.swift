import Foundation

public struct StudyInsights: Equatable, Sendable {
    public let coverage: StudyCoverage
    public let areas: [StudyAreaInsight]      // weakest first (see ordering)
    public let due: StudyDueCounts
    public let recentMisses: [QuestionIdentity]   // newest first, capped at 50
    public let activity: [StudyDay]               // oldest -> newest, exactly `activityDays` entries

    public init(
        coverage: StudyCoverage,
        areas: [StudyAreaInsight],
        due: StudyDueCounts,
        recentMisses: [QuestionIdentity],
        activity: [StudyDay]
    ) {
        self.coverage = coverage
        self.areas = areas
        self.due = due
        self.recentMisses = recentMisses
        self.activity = activity
    }
}

public struct StudyCoverage: Equatable, Sendable {
    public let totalQuestions: Int   // questions in `catalog`
    public let seen: Int             // distinct catalog questions with answered > 0
    public let answered: Int         // total attempts across catalog questions
    public let correct: Int

    public init(totalQuestions: Int, seen: Int, answered: Int, correct: Int) {
        self.totalQuestions = totalQuestions
        self.seen = seen
        self.answered = answered
        self.correct = correct
    }
}

public struct StudyAreaInsight: Equatable, Sendable {
    public let area: String          // QuestionMetadata.examArea
    public let totalQuestions: Int   // catalog questions in this area
    public let seen: Int
    public let answered: Int
    public let correct: Int

    /// Derived rather than stored, so two insights with the same counts can
    /// never compare unequal because of a caller-supplied accuracy.
    public var accuracy: Double { answered == 0 ? 0 : Double(correct) / Double(answered) }

    public init(area: String, totalQuestions: Int, seen: Int, answered: Int, correct: Int) {
        self.area = area
        self.totalQuestions = totalQuestions
        self.seen = seen
        self.answered = answered
        self.correct = correct
    }
}

public struct StudyDueCounts: Equatable, Sendable {
    public let due: Int              // has SRS state and nextDueAt <= now
    public let upcoming: Int         // has SRS state and nextDueAt > now
    public let unscheduled: Int      // catalog question with no SRS state

    public init(due: Int, upcoming: Int, unscheduled: Int) {
        self.due = due
        self.upcoming = upcoming
        self.unscheduled = unscheduled
    }
}

public struct StudyDay: Equatable, Sendable {
    public let day: Date             // start of that day in `calendar`
    public let answered: Int
    public let correct: Int

    public init(day: Date, answered: Int, correct: Int) {
        self.day = day
        self.answered = answered
        self.correct = correct
    }
}

public extension StudyInsights {
    static func derive(
        envelope: ProgressEnvelope?,
        catalog: [QuestionIdentity: Question],
        pending: [SessionAnswer] = [],
        now: Date,
        calendar: Calendar = Calendar(identifier: .gregorian),
        activityDays: Int = 14
    ) -> StudyInsights {
        // Questions from uninstalled packs are filtered out so insights reflect only the currently active catalog.
        var questionAnswered: [QuestionIdentity: Int] = [:]
        var questionCorrect: [QuestionIdentity: Int] = [:]

        if let mastery = envelope?.mastery {
            for m in mastery where catalog[m.identity] != nil {
                questionAnswered[m.identity, default: 0] += m.answered
                questionCorrect[m.identity, default: 0] += m.correct
            }
        }

        for p in pending where catalog[p.identity] != nil {
            questionAnswered[p.identity, default: 0] += 1
            if p.correct {
                questionCorrect[p.identity, default: 0] += 1
            }
        }

        let totalQuestions = catalog.count
        var seenCount = 0
        var totalAnswered = 0
        var totalCorrect = 0

        for id in catalog.keys {
            let ans = questionAnswered[id, default: 0]
            let cor = questionCorrect[id, default: 0]
            if ans > 0 {
                seenCount += 1
            }
            totalAnswered += ans
            totalCorrect += cor
        }

        let coverage = StudyCoverage(
            totalQuestions: totalQuestions,
            seen: seenCount,
            answered: totalAnswered,
            correct: totalCorrect
        )

        var areaQuestions: [String: [QuestionIdentity]] = [:]
        for (id, question) in catalog {
            areaQuestions[question.metadata.examArea, default: []].append(id)
        }

        var areaInsights: [StudyAreaInsight] = []
        for (area, identities) in areaQuestions {
            var areaSeen = 0
            var areaAnswered = 0
            var areaCorrect = 0
            for id in identities {
                let ans = questionAnswered[id, default: 0]
                let cor = questionCorrect[id, default: 0]
                if ans > 0 {
                    areaSeen += 1
                }
                areaAnswered += ans
                areaCorrect += cor
            }
            areaInsights.append(
                StudyAreaInsight(
                    area: area,
                    totalQuestions: identities.count,
                    seen: areaSeen,
                    answered: areaAnswered,
                    correct: areaCorrect
                )
            )
        }

        // Unattempted areas are unknown rather than weak, so they sort after measured areas.
        areaInsights.sort { a, b in
            if (a.answered == 0) != (b.answered == 0) {
                return a.answered > 0
            }
            if a.accuracy != b.accuracy {
                return a.accuracy < b.accuracy
            }
            if a.answered != b.answered {
                return a.answered > b.answered
            }
            return a.area < b.area
        }

        var srsByIdentity: [QuestionIdentity: SRSState] = [:]
        if let srs = envelope?.srs {
            for item in srs where catalog[item.identity] != nil {
                srsByIdentity[item.identity] = item.state
            }
        }

        var dueCount = 0
        var upcomingCount = 0
        var unscheduledCount = 0

        for id in catalog.keys {
            if let state = srsByIdentity[id] {
                // The schedule boundary is inclusive at `now`.
                if state.nextDueAt <= now {
                    dueCount += 1
                } else {
                    upcomingCount += 1
                }
            } else {
                unscheduledCount += 1
            }
        }

        let due = StudyDueCounts(
            due: dueCount,
            upcoming: upcomingCount,
            unscheduled: unscheduledCount
        )

        let recentMisses = extractRecentMisses(
            sessionDetails: envelope?.sessionDetails ?? [],
            catalog: catalog,
            limit: 50
        )

        let activity = computeActivity(
            sessionDetails: envelope?.sessionDetails ?? [],
            pending: pending,
            catalog: catalog,
            now: now,
            calendar: calendar,
            activityDays: activityDays
        )

        return StudyInsights(
            coverage: coverage,
            areas: areaInsights,
            due: due,
            recentMisses: recentMisses,
            activity: activity
        )
    }

    /// Questions worth retrying, best-effort, newest evidence first.
    static func missedQueue(
        envelope: ProgressEnvelope?,
        catalog: [QuestionIdentity: Question],
        limit: Int
    ) -> [QuestionIdentity] {
        guard limit > 0 else { return [] }

        var result: [QuestionIdentity] = []
        var taken = Set<QuestionIdentity>()

        // Tier 1 uses recent session misses; Tier 2 tops up from cumulative mastery.
        let tier1 = extractRecentMisses(
            sessionDetails: envelope?.sessionDetails ?? [],
            catalog: catalog,
            limit: min(50, limit)
        )
        for id in tier1 {
            result.append(id)
            taken.insert(id)
        }

        if result.count >= limit {
            return result
        }

        guard let mastery = envelope?.mastery else {
            return result
        }

        var masteryAttempts: [QuestionIdentity: (answered: Int, correct: Int)] = [:]
        for m in mastery where catalog[m.identity] != nil {
            masteryAttempts[m.identity, default: (0, 0)].answered += m.answered
            masteryAttempts[m.identity, default: (0, 0)].correct += m.correct
        }

        var candidates: [(identity: QuestionIdentity, misses: Int)] = []
        for (id, counts) in masteryAttempts {
            guard !taken.contains(id), counts.correct < counts.answered else { continue }
            candidates.append((identity: id, misses: counts.answered - counts.correct))
        }

        candidates.sort { a, b in
            if a.misses != b.misses {
                return a.misses > b.misses
            }
            return a.identity.description < b.identity.description
        }

        for candidate in candidates {
            result.append(candidate.identity)
            if result.count == limit {
                break
            }
        }

        return result
    }
}

private extension StudyInsights {
    static func extractRecentMisses(
        sessionDetails: [SessionDetail],
        catalog: [QuestionIdentity: Question],
        limit: Int
    ) -> [QuestionIdentity] {
        guard limit > 0 else { return [] }

        // Reverse-order traversal ensures the first seen answer for an identity is its latest attempt.
        let sortedSessions = sessionDetails.sorted { a, b in
            if a.completedAt != b.completedAt {
                return a.completedAt > b.completedAt
            }
            return a.id > b.id
        }

        var seen = Set<QuestionIdentity>()
        var misses: [QuestionIdentity] = []

        for session in sortedSessions {
            for answer in session.answers.reversed() {
                guard catalog[answer.identity] != nil else { continue }
                if seen.insert(answer.identity).inserted {
                    if !answer.correct {
                        misses.append(answer.identity)
                        if misses.count == limit {
                            return misses
                        }
                    }
                }
            }
        }

        return misses
    }

    static func computeActivity(
        sessionDetails: [SessionDetail],
        pending: [SessionAnswer],
        catalog: [QuestionIdentity: Question],
        now: Date,
        calendar: Calendar,
        activityDays: Int
    ) -> [StudyDay] {
        guard activityDays > 0 else { return [] }

        let todayStart = calendar.startOfDay(for: now)

        var days: [Date] = []
        days.reserveCapacity(activityDays)
        for i in 0..<activityDays {
            let offset = i - (activityDays - 1)
            let date = calendar.date(byAdding: .day, value: offset, to: todayStart) ?? todayStart
            days.append(calendar.startOfDay(for: date))
        }

        var dayMap: [Date: (answered: Int, correct: Int)] = [:]
        for day in days {
            dayMap[day] = (0, 0)
        }

        for session in sessionDetails {
            let sessionDay = calendar.startOfDay(for: session.completedAt)
            guard dayMap[sessionDay] != nil else { continue }
            for answer in session.answers where catalog[answer.identity] != nil {
                dayMap[sessionDay]!.answered += 1
                if answer.correct {
                    dayMap[sessionDay]!.correct += 1
                }
            }
        }

        for answer in pending where catalog[answer.identity] != nil {
            guard dayMap[todayStart] != nil else { continue }
            dayMap[todayStart]!.answered += 1
            if answer.correct {
                dayMap[todayStart]!.correct += 1
            }
        }

        return days.map { day in
            let counts = dayMap[day] ?? (0, 0)
            return StudyDay(day: day, answered: counts.answered, correct: counts.correct)
        }
    }
}
