import Foundation

/// Redacted states exposed by the sync repository. No case carries a record
/// name, account identifier, question identity, or server error text.
public enum SyncStatusState: String, Codable, CaseIterable, Sendable, Equatable {
    case idle
    case syncing
    case synced
    case offline
    case accountUnavailable = "account_unavailable"
    case rebasing
    case recoveryRequired = "recovery_required"
    case accountIsolationRequired = "account_isolation_required"
    case conflict
    case partialFailure = "partial_failure"
    case retryScheduled = "retry_scheduled"
    case failed
}

public enum SyncStatusReason: String, Codable, CaseIterable, Sendable, Equatable {
    case initialised
    case explicitFetch = "explicit_fetch"
    case explicitSend = "explicit_send"
    case statePersisted = "state_persisted"
    case statePersistenceFailed = "state_persistence_failed"
    case accountChanged = "account_changed"
    case containerChanged = "container_changed"
    case tokenExpired = "token_expired"
    case serverRecordChanged = "server_record_changed"
    case recordFailure = "record_failure"
    case unreachable
    case reachable
    case retryBackoff = "retry_backoff"
    case completed
    case incompatibleVersion = "incompatible_version"
    case malformedRecord = "malformed_record"
    case encodedSizeRefused = "encoded_size_refused"
    case rebaseRequired = "rebase_required"
}

/// A typed, privacy-safe status event suitable for every UI surface. Counts
/// and a relative retry delay are useful to presentation code without leaking
/// user data. `retryAfterMilliseconds` is relative, not an account timestamp.
public struct SyncStatusEvent: Codable, Sendable, Equatable {
    public let state: SyncStatusState
    public let reason: SyncStatusReason
    public let pendingOperationCount: Int
    public let pendingIssueCount: Int
    public let retryAttempt: Int
    public let retryAfterMilliseconds: Int?

    public init(
        state: SyncStatusState,
        reason: SyncStatusReason,
        pendingOperationCount: Int = 0,
        pendingIssueCount: Int = 0,
        retryAttempt: Int = 0,
        retryAfterMilliseconds: Int? = nil
    ) {
        self.state = state
        self.reason = reason
        self.pendingOperationCount = max(0, pendingOperationCount)
        self.pendingIssueCount = max(0, pendingIssueCount)
        self.retryAttempt = max(0, retryAttempt)
        self.retryAfterMilliseconds = retryAfterMilliseconds.map { max(0, $0) }
    }

    /// A stable JSON representation is useful for logs and test fixtures. It
    /// intentionally contains only the public fields above.
    public var redactedPayload: [String: String] {
        var payload: [String: String] = [
            "state": state.rawValue,
            "reason": reason.rawValue,
            "pending_operation_count": String(pendingOperationCount),
            "pending_issue_count": String(pendingIssueCount),
            "retry_attempt": String(retryAttempt)
        ]
        if let retryAfterMilliseconds {
            payload["retry_after_ms"] = String(retryAfterMilliseconds)
        }
        return payload
    }
}

/// Retry delays are deterministic and have no random jitter, which keeps
/// offline behavior reproducible in tests and makes the user-visible retry
/// state honest.
public struct CloudProgressRetryPolicy: Codable, Sendable, Equatable {
    public let baseDelayMilliseconds: Int
    public let maximumDelayMilliseconds: Int
    public let maximumAttempts: Int

    public init(
        baseDelayMilliseconds: Int = 1_000,
        maximumDelayMilliseconds: Int = 60_000,
        maximumAttempts: Int = 5
    ) {
        self.baseDelayMilliseconds = max(0, baseDelayMilliseconds)
        self.maximumDelayMilliseconds = max(self.baseDelayMilliseconds, maximumDelayMilliseconds)
        self.maximumAttempts = max(1, maximumAttempts)
    }

    public func delayMilliseconds(forAttempt attempt: Int) -> Int {
        guard attempt > 0 else { return 0 }
        let exponent = min(attempt - 1, 30)
        let multiplier = 1 << exponent
        return min(maximumDelayMilliseconds, baseDelayMilliseconds * multiplier)
    }
}
