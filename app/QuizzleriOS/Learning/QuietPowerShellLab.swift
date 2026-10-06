import Foundation
import SwiftUI
import QuizzlerKit

public enum QuietPowerShellLabKeys {
    public static let completed = "cysa004.lab.v1.quietpowershell.completed"
    public static let handoffNote = "cysa004.lab.v1.quietpowershell.handoff_note"
    public static let submittedResponseID = "cysa004.lab.v1.quietpowershell.submitted_response_id"
}

// MARK: - View Extension

extension View {
    func labCard(border: Color = QuizzlerTheme.border, lineWidth: CGFloat = 1) -> some View {
        padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
            .overlay(RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius).stroke(border, lineWidth: lineWidth))
    }
}

// MARK: - Root View

public struct QuietPowerShellLabView: View {
    @Environment(\.dismiss) private var dismiss
    @AppStorage(QuietPowerShellLabKeys.completed) private var isCompleted = false
    @AppStorage(QuietPowerShellLabKeys.handoffNote) private var persistedHandoffNote = ""
    @AppStorage(QuietPowerShellLabKeys.submittedResponseID) private var persistedSubmittedResponseID = ""

    @State private var caseData: QuietPowerShellCase?
    @State private var loadFailure: String?
    @State private var phase: LabPhase = .lesson
    // periphery:ignore - Projected binding is consumed by LabConceptCheckView.
    @State private var conceptSelections: [String: Bool] = [:]
    // periphery:ignore - Projected binding is consumed by LabCaseInvestigationView.
    @State private var selectedSourceID: String
    @State private var pinnedItemIDs: Set<String> = []
    // periphery:ignore - Projected binding is consumed by LabCaseInvestigationView.
    @State private var expandedItemID: String?
    @State private var selectedResponseID: String?
    @State private var executedScopeQueryID: String?
    @State private var currentNote: String = ""
    @State private var unlockedPhases: Set<LabPhase> = [.lesson]
    @State private var replayConfirmationPresented = false
    @State private var didRestore = false
    private let progressStore = LabProgressStore()

    public init() {
        do {
            let loadedCase = try QuietPowerShellCaseLoader.loadBundled()
            _caseData = State(initialValue: loadedCase)
            _selectedSourceID = State(initialValue: loadedCase.sources.first?.id ?? "")
            _loadFailure = State(initialValue: nil)
        } catch {
            _caseData = State(initialValue: nil)
            _selectedSourceID = State(initialValue: "")
            _loadFailure = State(initialValue: error.localizedDescription)
        }
    }

    private func pinnedCategories(in data: QuietPowerShellCase) -> Set<String> {
        Set(data.evidenceItems.filter { pinnedItemIDs.contains($0.id) }.map(\.sourceID))
    }

    private func canSubmitHandoff(in data: QuietPowerShellCase) -> Bool {
        guard let selectedResponseID,
              data.responses.contains(where: { $0.id == selectedResponseID }),
              data.debrief.responseEvaluations.contains(where: { $0.responseID == selectedResponseID }) else {
            return false
        }
        return pinnedCategories(in: data).count >= 2 && executedScopeQueryID != nil &&
            currentNote.trimmingCharacters(in: .whitespacesAndNewlines).count >= 20
    }

    public var body: some View {
        NavigationStack {
            Group {
                if let data = caseData {
                    VStack(spacing: 0) {
                        labHeader
                        phaseBar
                        Divider().overlay(QuizzlerTheme.border)

                        ScrollView {
                            VStack(alignment: .leading, spacing: 16) {
                                switch phase {
                                case .lesson:
                                    LabLessonView(data: data, onContinue: { advance(to: .check) })
                                case .check:
                                    LabConceptCheckView(data: data, selections: $conceptSelections, onContinue: { advance(to: .investigation) })
                                case .investigation:
                                    LabCaseInvestigationView(
                                        data: data, selectedSourceID: $selectedSourceID, pinnedItemIDs: $pinnedItemIDs,
                                        expandedItemID: $expandedItemID, selectedResponseID: $selectedResponseID,
                                        executedScopeQueryID: $executedScopeQueryID, handoffNote: $currentNote,
                                        pinnedCategories: pinnedCategories(in: data)
                                    )
                                case .debrief:
                                    LabDebriefView(data: data, selectedResponseID: selectedResponseID,
                                                   handoffNote: currentNote, onReplay: { replayConfirmationPresented = true })
                                }
                            }
                            .padding(QuizzlerTheme.pageGutter)
                            .padding(.bottom, QuizzlerTheme.scrollBottomInset)
                        }
                        .accessibilityIdentifier("lab-content-scroll")
                        .safeAreaInset(edge: .bottom) {
                            if phase == .investigation {
                                LabInvestigationStatusBar(
                                    pinnedCategoryCount: pinnedCategories(in: data).count,
                                    hasResponse: selectedResponseID != nil,
                                    hasExecutedQuery: executedScopeQueryID != nil,
                                    noteCharacterCount: currentNote.trimmingCharacters(in: .whitespacesAndNewlines).count,
                                    canSubmitHandoff: canSubmitHandoff(in: data),
                                    onSubmitHandoff: { submitHandoff(for: data) }
                                )
                            }
                        }
                    }
                    .background(QuizzlerTheme.terminalBackground.ignoresSafeArea())
                } else {
                    caseLoadFailureView
                }
            }
            .navigationTitle(caseData?.title ?? "CS0-004 Learning Lab")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Back to Today") { dismiss() }
                        .foregroundStyle(QuizzlerTheme.primaryCyan)
                        .accessibilityIdentifier("lab-exit-button")
                }
            }
            .onAppear { restoreOnce() }
            .onChange(of: progressSnapshot) { _, snapshot in
                persist(snapshot)
            }
        }
        .preferredColorScheme(.dark)
        .alert(
            "Replay investigation?",
            isPresented: $replayConfirmationPresented
        ) {
            Button("Replay", role: .destructive) { replayLab() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Replaying clears your pinned evidence, selected response and executed query so you can run the investigation again.")
        }
    }

    private var caseLoadFailureView: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Practice case unavailable", systemImage: "exclamationmark.triangle.fill")
                .font(.headline).foregroundStyle(QuizzlerTheme.warning)
            Text(loadFailure ?? "The CS0-004 practice case could not be loaded.")
                .font(.footnote).foregroundStyle(QuizzlerTheme.textMuted).fixedSize(horizontal: false, vertical: true)
            Text("This practice activity is unavailable until its bundled content passes validation.")
                .font(.caption).foregroundStyle(QuizzlerTheme.textMuted)
        }
        .padding(QuizzlerTheme.pageGutter)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(QuizzlerTheme.terminalBackground.ignoresSafeArea())
        .accessibilityIdentifier("lab-content-error")
    }

    private var labHeader: some View {
        HStack(spacing: 8) {
            badge("Synthetic practice", color: QuizzlerTheme.primaryCyan)
            badge("Not exam readiness", color: QuizzlerTheme.warning)
            Spacer()
            if isCompleted {
                Label("Completed", systemImage: "checkmark.circle.fill")
                    .font(.caption2.weight(.semibold)).foregroundStyle(QuizzlerTheme.success)
            }
        }
        .padding(.horizontal, QuizzlerTheme.pageGutter).padding(.vertical, 8)
        .background(QuizzlerTheme.elevatedCard)
    }

    private func badge(_ text: String, color: Color) -> some View {
        Text(text).font(.caption2.weight(.bold))
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(QuizzlerTheme.raisedCard, in: Capsule())
            .overlay(Capsule().stroke(color, lineWidth: 1)).foregroundStyle(color)
    }

    private var phaseBar: some View {
        HStack(spacing: 4) {
            ForEach(LabPhase.allCases) { item in
                let isUnlocked = unlockedPhases.contains(item)
                Button { phase = item } label: {
                    Text(item.rawValue).font(.caption.weight(phase == item ? .bold : .regular))
                        .foregroundStyle(phase == item ? QuizzlerTheme.primaryCyan : QuizzlerTheme.textMuted)
                        .frame(maxWidth: .infinity, minHeight: QuizzlerTheme.minimumTouchTarget)
                        .background(phase == item ? QuizzlerTheme.raisedCard : .clear, in: RoundedRectangle(cornerRadius: 6))
                }
                .buttonStyle(.plain)
                .disabled(!isUnlocked)
                .opacity(isUnlocked ? 1.0 : 0.4)
                .accessibilityHint(isUnlocked ? "" : lockedPhaseReason(for: item))
            }
        }
        .padding(6).background(QuizzlerTheme.elevatedCard)
    }

    private func lockedPhaseReason(for target: LabPhase) -> String {
        switch target {
        case .lesson:
            return "Return to the lesson."
        case .check:
            return "Finish the lesson and tap continue to unlock the concept check."
        case .investigation:
            return "Answer every concept check and tap continue to unlock the investigation."
        case .debrief:
            return "Submit the handoff to unlock the debrief."
        }
    }

    private func advance(to next: LabPhase) {
        unlockedPhases.insert(next)
        phase = next
    }

    private var progressSnapshot: QuietPowerShellLabProgress {
        QuietPowerShellLabProgress(
            schemaVersion: QuietPowerShellLabProgress.currentSchemaVersion,
            phase: phase,
            unlockedPhases: unlockedPhases,
            conceptSelections: conceptSelections,
            selectedSourceID: selectedSourceID,
            pinnedItemIDs: pinnedItemIDs.sorted(),
            selectedResponseID: selectedResponseID,
            executedScopeQueryID: executedScopeQueryID,
            currentNote: currentNote
        )
    }

    private func restoreOnce() {
        guard !didRestore else { return }
        didRestore = true
        if isCompleted {
            if currentNote.isEmpty && !persistedHandoffNote.isEmpty { currentNote = persistedHandoffNote }
            restoreCompletedDebriefIfAvailable()
        } else {
            restoreInProgressIfAvailable()
            if currentNote.isEmpty && !persistedHandoffNote.isEmpty { currentNote = persistedHandoffNote }
        }
    }

    private func restoreInProgressIfAvailable() {
        guard let data = caseData,
              let saved = progressStore.load(),
              let progress = saved.validated(against: data) else { return }
        phase = progress.phase
        unlockedPhases = progress.unlockedPhases
        conceptSelections = progress.conceptSelections
        selectedSourceID = progress.selectedSourceID
        pinnedItemIDs = Set(progress.pinnedItemIDs)
        selectedResponseID = progress.selectedResponseID
        executedScopeQueryID = progress.executedScopeQueryID
        currentNote = progress.currentNote
    }

    private func persist(_ snapshot: QuietPowerShellLabProgress) {
        if isCompleted {
            progressStore.clear()
        } else {
            progressStore.save(snapshot)
        }
    }

    private func submitHandoff(for data: QuietPowerShellCase) {
        guard canSubmitHandoff(in: data), let selectedResponseID else { return }
        progressStore.clear()
        isCompleted = true
        persistedHandoffNote = currentNote
        persistedSubmittedResponseID = selectedResponseID
        unlockedPhases.insert(.debrief)
        phase = .debrief
    }

    private func restoreCompletedDebriefIfAvailable() {
        guard isCompleted,
              let data = caseData,
              data.responses.contains(where: { $0.id == persistedSubmittedResponseID }),
              data.debrief.responseEvaluations.contains(where: { $0.responseID == persistedSubmittedResponseID }) else {
            return
        }
        selectedResponseID = persistedSubmittedResponseID
        unlockedPhases = Set(LabPhase.allCases)
        phase = .debrief
    }

    private func replayLab() {
        progressStore.clear()
        pinnedItemIDs.removeAll()
        selectedResponseID = nil
        persistedSubmittedResponseID = ""
        executedScopeQueryID = nil
        unlockedPhases = [.lesson, .check, .investigation]
        phase = .investigation
    }
}
