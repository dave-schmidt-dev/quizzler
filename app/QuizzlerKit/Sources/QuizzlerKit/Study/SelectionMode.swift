import Foundation

/// Explicit selection contracts. SRS is independent of normal and retry
/// selection and does not mutate mastery.
public enum SelectionMode: String, Codable, CaseIterable, Sendable {
    case normal
    case retryMissed = "retry_missed"
    case srs
}

public enum SRSRating: String, Codable, CaseIterable, Sendable { case again, hard, good, easy }

public struct SRSState: Codable, Equatable, Sendable {
    public let tier: Int
    public let nextDueAt: Date
    public let lastReviewedAt: Date?
    public let intervalDays: Int
    public let reviewCount: Int
    public init(tier: Int = 1, nextDueAt: Date, lastReviewedAt: Date? = nil, intervalDays: Int = 1, reviewCount: Int = 0) throws {
        guard (1...7).contains(tier), intervalDays > 0, reviewCount >= 0 else { throw SRSContractError.invalidState }
        self.tier = tier; self.nextDueAt = nextDueAt; self.lastReviewedAt = lastReviewedAt; self.intervalDays = intervalDays; self.reviewCount = reviewCount
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let tier = try container.decode(Int.self, forKey: .tier)
        let nextDueAt = try container.decode(Date.self, forKey: .nextDueAt)
        let lastReviewedAt = try container.decodeIfPresent(Date.self, forKey: .lastReviewedAt)
        let intervalDays = try container.decode(Int.self, forKey: .intervalDays)
        let reviewCount = try container.decode(Int.self, forKey: .reviewCount)
        do {
            try self.init(
                tier: tier,
                nextDueAt: nextDueAt,
                lastReviewedAt: lastReviewedAt,
                intervalDays: intervalDays,
                reviewCount: reviewCount
            )
        } catch {
            throw DecodingError.dataCorrupted(.init(
                codingPath: decoder.codingPath,
                debugDescription: "invalid SRS state"
            ))
        }
    }

    private enum CodingKeys: String, CodingKey {
        case tier, nextDueAt, lastReviewedAt, intervalDays, reviewCount
    }
}

public enum SRSContractError: Error, Equatable, Sendable { case invalidState, invalidLimit }

public struct SelectionRequest: Codable, Equatable, Sendable {
    public let mode: SelectionMode
    public let limit: Int
    public init(mode: SelectionMode, limit: Int) throws { guard limit > 0 else { throw SRSContractError.invalidLimit }; self.mode = mode; self.limit = limit }
}
