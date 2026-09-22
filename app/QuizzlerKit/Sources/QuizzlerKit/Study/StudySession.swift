import Foundation

public struct StudySessionPlan: Equatable, Sendable {
    public let mode: SelectionMode
    /// At most `request.limit` questions. This is a batch, not the queue.
    public let questions: [QuestionIdentity]

    public init(mode: SelectionMode, questions: [QuestionIdentity]) {
        self.mode = mode
        self.questions = questions
    }
}

public extension StudySessionPlan {
    static func build(
        request: SelectionRequest,
        envelope: ProgressEnvelope?,
        catalog: [QuestionIdentity: Question],
        packOrder: [QuestionIdentity],
        resumeIndex: Int = 0,
        excluding: Set<QuestionIdentity> = [],
        now: Date
    ) -> StudySessionPlan {
        switch request.mode {
        case .normal:
            return buildNormal(
                request: request,
                catalog: catalog,
                packOrder: packOrder,
                resumeIndex: resumeIndex,
                excluding: excluding
            )
        case .srs:
            return buildSRS(
                request: request,
                envelope: envelope,
                catalog: catalog,
                excluding: excluding,
                now: now
            )
        case .retryMissed:
            return buildRetryMissed(
                request: request,
                envelope: envelope,
                catalog: catalog,
                excluding: excluding
            )
        case .weakAreas:
            return buildWeakAreas(
                request: request,
                envelope: envelope,
                catalog: catalog,
                excluding: excluding,
                now: now
            )
        }
    }
}

private extension StudySessionPlan {
    static func buildNormal(
        request: SelectionRequest,
        catalog: [QuestionIdentity: Question],
        packOrder: [QuestionIdentity],
        resumeIndex: Int,
        excluding: Set<QuestionIdentity>
    ) -> StudySessionPlan {
        var seen = Set<QuestionIdentity>()
        var validOrder: [QuestionIdentity] = []
        for id in packOrder {
            if catalog[id] != nil && seen.insert(id).inserted {
                validOrder.append(id)
            }
        }
        guard !validOrder.isEmpty else {
            return StudySessionPlan(mode: .normal, questions: [])
        }

        let n = validOrder.count
        let startIndex = ((resumeIndex % n) + n) % n
        var questions: [QuestionIdentity] = []
        for i in 0..<n {
            let id = validOrder[(startIndex + i) % n]
            if !excluding.contains(id) {
                questions.append(id)
                if questions.count == request.limit {
                    break
                }
            }
        }

        return StudySessionPlan(mode: .normal, questions: questions)
    }

    static func buildSRS(
        request: SelectionRequest,
        envelope: ProgressEnvelope?,
        catalog: [QuestionIdentity: Question],
        excluding: Set<QuestionIdentity>,
        now: Date
    ) -> StudySessionPlan {
        var srsByIdentity: [QuestionIdentity: SRSState] = [:]
        if let srs = envelope?.srs {
            for item in srs where catalog[item.identity] != nil {
                srsByIdentity[item.identity] = item.state
            }
        }

        var dueCandidates: [(identity: QuestionIdentity, nextDueAt: Date)] = []
        for (id, state) in srsByIdentity {
            if state.nextDueAt <= now && !excluding.contains(id) {
                dueCandidates.append((identity: id, nextDueAt: state.nextDueAt))
            }
        }

        dueCandidates.sort { a, b in
            if a.nextDueAt != b.nextDueAt {
                return a.nextDueAt < b.nextDueAt
            }
            return a.identity.description < b.identity.description
        }

        let questions = Array(dueCandidates.prefix(request.limit).map(\.identity))
        return StudySessionPlan(mode: .srs, questions: questions)
    }

    static func buildRetryMissed(
        request: SelectionRequest,
        envelope: ProgressEnvelope?,
        catalog: [QuestionIdentity: Question],
        excluding: Set<QuestionIdentity>
    ) -> StudySessionPlan {
        let effectiveCatalog = excluding.isEmpty
            ? catalog
            : catalog.filter { !excluding.contains($0.key) }
        let questions = StudyInsights.missedQueue(
            envelope: envelope,
            catalog: effectiveCatalog,
            limit: request.limit
        )
        return StudySessionPlan(mode: .retryMissed, questions: questions)
    }

    static func buildWeakAreas(
        request: SelectionRequest,
        envelope: ProgressEnvelope?,
        catalog: [QuestionIdentity: Question],
        excluding: Set<QuestionIdentity>,
        now: Date
    ) -> StudySessionPlan {
        let insights = StudyInsights.derive(
            envelope: envelope,
            catalog: catalog,
            now: now
        )

        var questionAnswered: [QuestionIdentity: Int] = [:]
        var questionCorrect: [QuestionIdentity: Int] = [:]
        if let mastery = envelope?.mastery {
            for m in mastery where catalog[m.identity] != nil {
                questionAnswered[m.identity, default: 0] += m.answered
                questionCorrect[m.identity, default: 0] += m.correct
            }
        }

        var areaQueues: [[QuestionIdentity]] = []
        for areaInsight in insights.areas {
            let questionsInArea = catalog.compactMap { (id, question) -> QuestionIdentity? in
                guard question.metadata.examArea == areaInsight.area, !excluding.contains(id) else {
                    return nil
                }
                return id
            }
            guard !questionsInArea.isEmpty else { continue }

            let sortedQuestions = questionsInArea.sorted { idA, idB in
                let ansA = questionAnswered[idA, default: 0]
                let ansB = questionAnswered[idB, default: 0]
                if (ansA == 0) != (ansB == 0) {
                    return ansA == 0
                }
                if ansA > 0 {
                    let accA = Double(questionCorrect[idA, default: 0]) / Double(ansA)
                    let accB = Double(questionCorrect[idB, default: 0]) / Double(ansB)
                    if accA != accB {
                        return accA < accB
                    }
                }
                return idA.description < idB.description
            }
            areaQueues.append(sortedQuestions)
        }

        var questions: [QuestionIdentity] = []
        var index = 0
        while questions.count < request.limit {
            var addedAny = false
            for queue in areaQueues {
                if index < queue.count {
                    questions.append(queue[index])
                    addedAny = true
                    if questions.count == request.limit {
                        break
                    }
                }
            }
            if !addedAny {
                break
            }
            index += 1
        }

        return StudySessionPlan(mode: .weakAreas, questions: questions)
    }
}
