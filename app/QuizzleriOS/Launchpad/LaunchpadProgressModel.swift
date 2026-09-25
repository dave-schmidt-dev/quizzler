import Foundation
import SwiftUI
import QuizzlerKit
import UIKit

/// The six top-level states in Launchpad A.
enum LaunchpadState: String, CaseIterable, Identifiable {
    case today
    case question
    case feedback
    case results
    case progress
    case settings

    var id: String { rawValue }

    var title: String {
        rawValue.capitalized
    }

    var icon: String {
        switch self {
        case .today: "sun.max"
        case .question: "questionmark.circle"
        case .feedback: "checkmark.message"
        case .results: "chart.bar"
        case .progress: "chart.line.uptrend.xyaxis"
        case .settings: "gearshape"
        }
    }

    /// Launchpad keeps the study flow in one state machine while exposing
    /// only the three persistent destinations from the locked design.
    static let primaryNavigationStates: [LaunchpadState] = [.today, .progress, .settings]
}

@MainActor
final class LaunchpadProgressModel: ObservableObject {
    enum PersistenceState: Equatable {
        case loading
        case local
        case saving
        case syncing
        case synced
        case syncPending
        case accountChanged
        case saveFailed
    }

    @Published private(set) var aggregate = AggregateSnapshot()
    @Published private(set) var unsavedAnswers: [SessionAnswer] = []
    @Published private(set) var persistenceState: PersistenceState = .loading
    @Published private(set) var isReadyForStudy = false

    private let repository: any LaunchpadProgressRepository
    private let beforeSave: @Sendable () async -> Void
    /// Published so study surfaces can derive insights from the same
    /// envelope the counters come from, rather than keeping a second copy
    /// that can drift from it.
    @Published private(set) var envelope: ProgressEnvelope?
    @Published private(set) var maximumLevelError: String?
    private var saveIsInFlight = false
    private var syncIsInFlight = false
    private var maximumLevelChangeIsInFlight = false
    private var progressStreamTask: Task<Void, Never>?
    private var syncStatusStreamTask: Task<Void, Never>?

    init(repository: any LaunchpadProgressRepository, beforeSave: @escaping @Sendable () async -> Void = {}) {
        self.repository = repository
        self.beforeSave = beforeSave
    }

    deinit {
        progressStreamTask?.cancel()
        syncStatusStreamTask?.cancel()
    }

    var answered: Int { aggregate.answered + unsavedAnswers.count }
    var correct: Int { aggregate.correct + unsavedAnswers.filter(\.correct).count }
    var maximumLeitnerLevel: Int { envelope?.maximumLeitnerLevel ?? LeitnerSchedule.defaultMaximumLevel }

    func aggregate(courseID: String, packID: String) -> AggregateSnapshot {
        let stored = envelope?.mastery.reduce(into: AggregateSnapshot()) { result, mastery in
            guard mastery.identity.courseID == courseID,
                  mastery.identity.packID == packID else { return }
            result.answered += mastery.answered
            result.correct += mastery.correct
        } ?? AggregateSnapshot()
        let pending = unsavedAnswers.filter {
            $0.identity.courseID == courseID && $0.identity.packID == packID
        }
        return AggregateSnapshot(
            answered: stored.answered + pending.count,
            correct: stored.correct + pending.filter(\.correct).count
        )
    }

    /// Returns every identity in that pack with a mastery entry (answered > 0)
    /// or a pending unsaved answer.
    func seenIdentities(courseID: String, packID: String) -> Set<QuestionIdentity> {
        var seen = Set<QuestionIdentity>()
        if let mastery = envelope?.mastery {
            for entry in mastery {
                if entry.identity.courseID == courseID,
                   entry.identity.packID == packID,
                   entry.answered > 0 {
                    seen.insert(entry.identity)
                }
            }
        }
        for answer in unsavedAnswers {
            if answer.identity.courseID == courseID,
               answer.identity.packID == packID {
                seen.insert(answer.identity)
            }
        }
        return seen
    }

    var persistenceStatus: String {
        Self.persistenceStatus(for: persistenceState)
    }

    static func persistenceStatus(for state: PersistenceState) -> String {
        switch state {
        case .loading: "loading local progress"
        case .local: "local progress saved"
        case .saving: "saving progress locally"
        case .syncing: "syncing progress"
        case .synced: "last sync succeeded"
        case .syncPending: "progress saved here · sync pending"
        case .accountChanged: "iCloud account changed · local history kept safe"
        case .saveFailed: "local save failed · retry required"
        }
    }

    func load() {
        persistenceState = .loading
        isReadyForStudy = false
        startProgressObservation()
        startSyncStatusObservation()
        Task {
            do {
                guard repository.syncMode == .cloudKit else {
                    let snapshot = try await repository.snapshot()
                    if snapshot.schemaVersion == 1 {
                        _ = try await repository.setMaximumLeitnerLevel(LeitnerSchedule.defaultMaximumLevel)
                    }
                    let readySnapshot = try await repository.snapshot()
                    guard readySnapshot.schemaVersion == ProgressEnvelope.currentSchemaVersion else {
                        throw ProgressRepositoryError.corruptState
                    }
                    apply(readySnapshot)
                    isReadyForStudy = true
                    persistenceState = .local
                    return
                }
                startSynchronization()
            } catch {
                isReadyForStudy = false
                persistenceState = .saveFailed
            }
        }
    }

    func record(_ answer: SessionAnswer) {
        unsavedAnswers.append(answer)
    }

    /// Records an answer and starts its durable save before feedback is shown.
    /// The in-flight guard makes a later Next-question save a no-op for this
    /// same answer while still allowing answers added afterward to drain.
    func recordAndSave(_ answer: SessionAnswer) {
        record(answer)
        saveCurrentSession()
    }

    func saveCurrentSession() {
        guard !unsavedAnswers.isEmpty else {
            if persistenceState == .saveFailed, !saveIsInFlight, !syncIsInFlight {
                load()
            } else if persistenceState == .syncPending {
                refreshCloudProgress()
            }
            return
        }
        // The current save or CloudKit fetch/send will finish first. It then
        // drains the answers accumulated while it was in flight, preserving
        // order without two tasks removing the same prefix.
        guard !saveIsInFlight, !syncIsInFlight else { return }
        persistNextBatch()
    }

    /// Re-fetches shared progress when the app returns to the foreground.
    /// The existing in-flight guards keep this from competing with a save or
    /// another synchronization already started by the launch lifecycle.
    func synchronizeOnForeground() {
        refreshCloudProgress()
    }

    /// Writes the selected cap through the progress repository so it is
    /// durable and shared with the learner's other devices.
    func setMaximumLeitnerLevel(_ maximum: Int) {
        guard (1...7).contains(maximum), isReadyForStudy,
              maximum != self.maximumLeitnerLevel,
              !saveIsInFlight, !syncIsInFlight, !maximumLevelChangeIsInFlight else { return }
        maximumLevelError = nil
        maximumLevelChangeIsInFlight = true
        let previousPersistenceState = persistenceState
        persistenceState = repository.syncMode == .cloudKit ? .syncing : .saving
        Task {
            do {
                let updated = try await repository.setMaximumLeitnerLevel(maximum)
                apply(updated)
                if repository.syncMode == .cloudKit {
                    try await repository.synchronize()
                    apply(try await repository.snapshot())
                    persistenceState = .synced
                } else {
                    apply(try await repository.snapshot())
                    persistenceState = .local
                }
            } catch {
                if let localSnapshot = try? await repository.snapshot() {
                    apply(localSnapshot)
                }
                if let cloudError = error as? CloudProgressRepositoryError,
                   case let .maximumLevelChangeTooLarge(affected, limit) = cloudError {
                    maximumLevelError = "This change would adjust \(affected) questions. The current sync limit is \(limit) questions per change."
                    persistenceState = previousPersistenceState
                } else {
                    persistenceState = repository.syncMode == .cloudKit ? .syncPending : .saveFailed
                }
            }
            maximumLevelChangeIsInFlight = false
        }
    }

    private func persistNextBatch() {
        guard !saveIsInFlight, !syncIsInFlight, !unsavedAnswers.isEmpty else { return }
        let batch = Array(unsavedAnswers)
        saveIsInFlight = true
        persistenceState = .saving
        Task {
            do {
                await beforeSave()
                _ = try await repository.save(SessionDetail(answers: batch))
                guard unsavedAnswers.count >= batch.count else {
                    saveIsInFlight = false
                    persistenceState = .saveFailed
                    return
                }
                unsavedAnswers.removeFirst(batch.count)
                apply(try await repository.snapshot())
                saveIsInFlight = false
                if !unsavedAnswers.isEmpty {
                    persistNextBatch()
                } else if repository.syncMode == .cloudKit {
                    startSynchronization()
                } else {
                    persistenceState = .local
                }
            } catch {
                saveIsInFlight = false
                persistenceState = .saveFailed
            }
        }
    }

    private func refreshCloudProgress() {
        guard repository.syncMode == .cloudKit, !saveIsInFlight else { return }
        startSynchronization()
    }

    private func startSynchronization() {
        guard repository.syncMode == .cloudKit, !saveIsInFlight, !syncIsInFlight else { return }
        syncIsInFlight = true
        persistenceState = .syncing
        Task {
            do {
                try await repository.synchronize()
                var authoritativeSnapshot = try await repository.snapshot()
                if authoritativeSnapshot.schemaVersion == 1 {
                    _ = try await repository.setMaximumLeitnerLevel(LeitnerSchedule.defaultMaximumLevel)
                    try await repository.synchronize()
                    authoritativeSnapshot = try await repository.snapshot()
                }
                guard authoritativeSnapshot.schemaVersion == ProgressEnvelope.currentSchemaVersion else {
                    throw ProgressRepositoryError.corruptState
                }
                apply(authoritativeSnapshot)
                isReadyForStudy = true
                syncIsInFlight = false
                if unsavedAnswers.isEmpty {
                    persistenceState = .synced
                } else {
                    persistNextBatch()
                }
            } catch {
                syncIsInFlight = false
                if !isReadyForStudy {
                    isReadyForStudy = false
                }
                if let error = error as? CloudProgressRepositoryError,
                   error == .accountIsolationRequired {
                    persistenceState = .accountChanged
                } else if unsavedAnswers.isEmpty {
                    // The CloudKit checkpoint is already durable. A failed
                    // transfer must not be presented as a failed local save.
                    persistenceState = .syncPending
                } else {
                    persistNextBatch()
                }
            }
        }
    }

    private func apply(_ envelope: ProgressEnvelope) {
        self.envelope = envelope
        aggregate = envelope.aggregate
    }

    /// Keeps visible counters current while a cloud-backed app remains open.
    /// The repository owns actor serialization and deduplication; this model
    /// has one cancellable consumer so reloads cannot leave stale listeners.
    private func startProgressObservation() {
        guard repository.syncMode == .cloudKit else { return }
        progressStreamTask?.cancel()
        let repository = self.repository
        progressStreamTask = Task { [weak self] in
            let stream = await repository.progressSnapshots()
            for await envelope in stream {
                guard !Task.isCancelled else { return }
                self?.apply(envelope)
            }
        }
    }

    /// Account isolation is a safety boundary, not a retryable network lapse.
    /// Surface it as soon as CloudKit reports the account transition so the
    /// user is not directed to retry an operation that cannot succeed.
    private func startSyncStatusObservation() {
        guard repository.syncMode == .cloudKit else { return }
        syncStatusStreamTask?.cancel()
        let repository = self.repository
        syncStatusStreamTask = Task { [weak self] in
            let stream = await repository.syncStatusEvents()
            for await status in stream {
                guard !Task.isCancelled else { return }
                guard status.reason == .accountChanged
                        || status.state == .accountIsolationRequired else { continue }
                self?.persistenceState = .accountChanged
            }
        }
    }
}

enum LaunchpadSyncMode: Sendable, Equatable {
    case local
    case cloudKit
}

protocol LaunchpadProgressRepository: Sendable {
    var syncMode: LaunchpadSyncMode { get }
    func snapshot() async throws -> ProgressEnvelope
    func progressSnapshots() async -> AsyncStream<ProgressEnvelope>
    func save(_ session: SessionDetail) async throws -> ProgressOperation
    func queueIssue(_ issue: QuestionIssue) async throws -> QuestionIssue
    func synchronize() async throws
    func setMaximumLeitnerLevel(_ maximum: Int) async throws -> ProgressEnvelope
    func reviewHistory(for identity: QuestionIdentity) async throws -> QuestionReviewHistory
    func syncStatusEvents() async -> AsyncStream<SyncStatusEvent>
}

extension LaunchpadProgressRepository {
    /// Persists a report before making one best-effort CloudKit send. A report
    /// must remain available for retry even when the network phase fails.
    func queueIssueAndScheduleSync(_ issue: QuestionIssue) async throws -> QuestionIssue {
        let queuedIssue = try await queueIssue(issue)
        guard syncMode == .cloudKit else { return queuedIssue }

        Task { [self] in
            try? await synchronize()
        }
        return queuedIssue
    }

    func syncStatusEvents() async -> AsyncStream<SyncStatusEvent> {
        AsyncStream { continuation in continuation.finish() }
    }

    /// Generic repositories may not retain immutable review events. Returning
    /// an incomplete empty history keeps that limitation explicit in the UI.
    func reviewHistory(for identity: QuestionIdentity) async throws -> QuestionReviewHistory {
        .incomplete([])
    }

    func setMaximumLeitnerLevel(_ maximum: Int) async throws -> ProgressEnvelope {
        throw ProgressRepositoryError.invalidOperation
    }
}

extension ProgressRepository: LaunchpadProgressRepository {
    nonisolated var syncMode: LaunchpadSyncMode { .local }

    func progressSnapshots() async -> AsyncStream<ProgressEnvelope> {
        AsyncStream { continuation in continuation.finish() }
    }

    func save(_ session: SessionDetail) async throws -> ProgressOperation {
        try await save(session, operationID: nil, now: Date())
    }

    func setMaximumLeitnerLevel(_ maximum: Int) async throws -> ProgressEnvelope {
        _ = try await setMaximumLeitnerLevel(maximum, operationID: nil, now: Date())
        return try await snapshot()
    }

    func synchronize() async throws {
        // The legacy local repository has no network phase. Keeping this a
        // protocol-level no-op makes test fixtures and offline previews use
        // the same launchpad lifecycle without claiming shared progress.
    }
}
