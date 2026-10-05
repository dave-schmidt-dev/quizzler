import SwiftUI

// MARK: - Lesson & Concept Check

struct LabLessonView: View {
    let data: QuietPowerShellCase
    let onContinue: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(data.lessonTitle).font(.title3.weight(.bold)).foregroundStyle(QuizzlerTheme.textPrimary)
            Text(data.lessonIntro).font(.subheadline).foregroundStyle(QuizzlerTheme.textMuted).fixedSize(horizontal: false, vertical: true)

            ForEach(data.lessonPrinciples) { item in
                VStack(alignment: .leading, spacing: 4) {
                    Text(item.title).font(.subheadline.weight(.semibold)).foregroundStyle(QuizzlerTheme.primaryCyan)
                    Text(item.body).font(.footnote).foregroundStyle(QuizzlerTheme.textMuted).fixedSize(horizontal: false, vertical: true)
                }
                .labCard()
            }

            VStack(alignment: .leading, spacing: 6) {
                Label("Mapped objectives and guidance", systemImage: "book.closed")
                    .font(.subheadline.weight(.semibold)).foregroundStyle(QuizzlerTheme.textPrimary)
                ForEach(data.objectiveReferences) { reference in
                    if let url = URL(string: reference.url) {
                        Link("\(reference.kind == "objective" ? "Objective \(reference.id)" : reference.id): \(reference.title)", destination: url)
                            .font(.caption).tint(QuizzlerTheme.primaryCyan)
                    }
                }
            }
            .labCard()

            Button(action: onContinue) {
                HStack { Text("Proceed to concept check"); Image(systemName: "arrow.right") }
                    .font(.body.weight(.semibold)).frame(maxWidth: .infinity, minHeight: 44)
            }
            .buttonStyle(.borderedProminent).tint(QuizzlerTheme.primaryCyan).foregroundStyle(.black)
            .accessibilityIdentifier("lab-lesson-continue")
        }
    }
}

struct LabConceptCheckView: View {
    let data: QuietPowerShellCase
    @Binding var selections: [String: Bool]
    let onContinue: () -> Void

    private var allAnswered: Bool {
        data.conceptChecks.allSatisfy { selections[$0.id] != nil }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(data.checkTitle).font(.title3.weight(.bold)).foregroundStyle(QuizzlerTheme.textPrimary)
            Text(data.checkPrompt).font(.subheadline).foregroundStyle(QuizzlerTheme.textMuted)

            ForEach(data.conceptChecks) { item in
                let selected = selections[item.id]
                let answered = selected != nil
                let isCorrect = selected == item.isObservation

                VStack(alignment: .leading, spacing: 8) {
                    Text(item.statement).font(.subheadline).foregroundStyle(QuizzlerTheme.textPrimary).fixedSize(horizontal: false, vertical: true)

                    HStack(spacing: 10) {
                        choiceButton(title: "Observation", isPicked: selected == true,
                                     identifier: "lab-check-\(item.id)-observation") { selections[item.id] = true }
                        choiceButton(title: "Inference", isPicked: selected == false,
                                     identifier: "lab-check-\(item.id)-inference") { selections[item.id] = false }
                    }

                    if answered {
                        HStack(alignment: .top, spacing: 6) {
                            Image(systemName: isCorrect ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                                .foregroundStyle(isCorrect ? QuizzlerTheme.success : QuizzlerTheme.warning)
                            Text(item.explanation).font(.caption).foregroundStyle(QuizzlerTheme.textMuted).fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                .labCard()
            }

            Button(action: onContinue) {
                HStack { Text("Begin case FIN-17 investigation"); Image(systemName: "arrow.right") }
                    .font(.body.weight(.semibold)).frame(maxWidth: .infinity, minHeight: 44)
            }
            .buttonStyle(.borderedProminent).tint(QuizzlerTheme.primaryCyan).foregroundStyle(.black)
            .disabled(!allAnswered).opacity(allAnswered ? 1.0 : 0.4)
            .accessibilityIdentifier("lab-concept-continue")
        }
    }

    private func choiceButton(title: String, isPicked: Bool, identifier: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title).font(.caption.weight(isPicked ? .bold : .medium))
                .foregroundStyle(isPicked ? .black : QuizzlerTheme.textPrimary)
                .frame(maxWidth: .infinity, minHeight: QuizzlerTheme.minimumTouchTarget)
                .background(isPicked ? QuizzlerTheme.primaryCyan : QuizzlerTheme.raisedCard, in: RoundedRectangle(cornerRadius: 6))
                .overlay(RoundedRectangle(cornerRadius: 6).stroke(QuizzlerTheme.border, lineWidth: 1))
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier(identifier)
    }
}

// MARK: - Investigation View

struct LabCaseInvestigationView: View {
    let data: QuietPowerShellCase
    @Binding var selectedSourceID: String
    @Binding var pinnedItemIDs: Set<String>
    @Binding var expandedItemID: String?
    @Binding var selectedResponseID: String?
    @Binding var executedScopeQueryID: String?
    @Binding var handoffNote: String
    let pinnedCategories: Set<String>

    private var filteredEvidence: [EvidenceItem] {
        data.evidenceItems.filter { item in
            guard item.sourceID == selectedSourceID else { return false }
            return !item.appearsAfterDismissal || selectedResponseID == "dismiss"
        }
    }

    private var selectedSource: LabEvidenceSource? { data.sources.first { $0.id == selectedSourceID } }
    private var selectedResponse: LabResponseChoice? { data.responses.first { $0.id == selectedResponseID } }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 4) {
                Text(data.caseTitle).font(.headline).foregroundStyle(QuizzlerTheme.textPrimary)
                Text(data.caseDescription)
                    .font(.footnote).foregroundStyle(QuizzlerTheme.textMuted).fixedSize(horizontal: false, vertical: true)
            }
            .labCard()

            sourceSelector
            consequenceNotice
            evidenceListSection
            pinnedLockerSection
            scopeQuerySection
            responseChoiceSection
            handoffNoteSection
        }
    }

    private var sourceSelector: some View {
        HStack(spacing: 6) {
            ForEach(data.sources) { src in
                Button { selectedSourceID = src.id } label: {
                    VStack(spacing: 3) {
                        Image(systemName: src.icon).font(.caption)
                        Text(src.title).font(.caption2.weight(selectedSourceID == src.id ? .bold : .medium))
                    }
                    .foregroundStyle(selectedSourceID == src.id ? QuizzlerTheme.primaryCyan : QuizzlerTheme.textMuted)
                    .frame(maxWidth: .infinity, minHeight: 44)
                    .background(selectedSourceID == src.id ? QuizzlerTheme.raisedCard : .clear, in: RoundedRectangle(cornerRadius: 6))
                    .overlay(RoundedRectangle(cornerRadius: 6).stroke(selectedSourceID == src.id ? QuizzlerTheme.primaryCyan : QuizzlerTheme.border, lineWidth: 1))
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("lab-source-\(src.id)")
            }
        }
    }

    @ViewBuilder
    private var consequenceNotice: some View {
        if let selectedResponse {
            let color: Color = switch selectedResponse.impact {
            case "success": QuizzlerTheme.success
            case "danger": QuizzlerTheme.danger
            case "warning": QuizzlerTheme.warning
            default: QuizzlerTheme.primaryCyan
            }
            banner(text: selectedResponse.statusMessage, color: color)
        }
    }

    private func banner(text: String, color: Color) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "info.circle.fill").foregroundStyle(color)
            Text(text).font(.caption).foregroundStyle(color)
        }
        .padding(10).frame(maxWidth: .infinity, alignment: .leading)
        .background(QuizzlerTheme.raisedCard, in: RoundedRectangle(cornerRadius: 6))
    }

    private var evidenceListSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("\(selectedSource?.title ?? "Evidence") Telemetry").font(.subheadline.weight(.semibold)).foregroundStyle(QuizzlerTheme.textPrimary)

            ForEach(filteredEvidence) { item in
                let isWiped = selectedResponseID == "reimage" && item.isUnavailableAfterReimage && !pinnedItemIDs.contains(item.id)
                let isPinned = pinnedItemIDs.contains(item.id)
                let isExpanded = expandedItemID == item.id

                VStack(alignment: .leading, spacing: 6) {
                    HStack(alignment: .top) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(item.timestamp).font(QuizzlerTheme.metadataFont).foregroundStyle(QuizzlerTheme.textMuted)
                            Text(item.summary).font(.subheadline.weight(.medium))
                                .foregroundStyle(isWiped ? QuizzlerTheme.textMuted : QuizzlerTheme.textPrimary)
                        }
                        Spacer()
                        if isWiped {
                            Text("[Wiped]").font(.caption2.weight(.bold)).foregroundStyle(QuizzlerTheme.danger)
                        } else {
                            Button {
                                if isPinned { pinnedItemIDs.remove(item.id) } else { pinnedItemIDs.insert(item.id) }
                            } label: {
                                HStack(spacing: 3) {
                                    Image(systemName: isPinned ? "pin.fill" : "pin")
                                    Text(isPinned ? "Pinned" : "Pin")
                                }
                                .font(.caption2.weight(.semibold)).padding(.horizontal, 8).padding(.vertical, 4)
                                .background(isPinned ? QuizzlerTheme.primaryCyan : QuizzlerTheme.raisedCard, in: Capsule())
                                .foregroundStyle(isPinned ? .black : QuizzlerTheme.primaryCyan)
                            }
                            .buttonStyle(.plain)
                            .accessibilityIdentifier("lab-pin-\(item.id)")
                        }
                    }

                    if isWiped {
                        Text(data.reimageEvidenceLossNote)
                            .font(.caption2).foregroundStyle(QuizzlerTheme.danger)
                    } else {
                        Button { expandedItemID = isExpanded ? nil : item.id } label: {
                            HStack(spacing: 4) {
                                Text(isExpanded ? "Hide details" : "View details").font(.caption2).foregroundStyle(QuizzlerTheme.primaryCyan)
                                Image(systemName: isExpanded ? "chevron.up" : "chevron.down").font(.caption2).foregroundStyle(QuizzlerTheme.primaryCyan)
                            }
                        }
                        .buttonStyle(.plain)

                        if isExpanded {
                            Text(item.details).font(QuizzlerTheme.metadataFont).foregroundStyle(QuizzlerTheme.textMuted)
                                .padding(8).frame(maxWidth: .infinity, alignment: .leading)
                                .background(QuizzlerTheme.terminalBackground, in: RoundedRectangle(cornerRadius: 4))
                        }
                    }
                }
                .labCard(border: isPinned ? QuizzlerTheme.primaryCyan : QuizzlerTheme.border, lineWidth: isPinned ? 1.5 : 1)
            }
        }
    }

    private var pinnedLockerSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Label("Pinned evidence locker", systemImage: "pin.fill").font(.subheadline.weight(.semibold)).foregroundStyle(QuizzlerTheme.textPrimary)
                Spacer()
                Text("\(pinnedCategories.count)/2+ sources").font(.caption.monospacedDigit())
                    .foregroundStyle(pinnedCategories.count >= 2 ? QuizzlerTheme.success : QuizzlerTheme.warning)
            }

            if pinnedItemIDs.isEmpty {
                Text("No items pinned. Pin at least two distinct sources (e.g. Authentication + Endpoint).").font(.caption).foregroundStyle(QuizzlerTheme.textMuted)
            } else {
                ForEach(data.evidenceItems.filter { pinnedItemIDs.contains($0.id) }) { pinned in
                    let source = data.sources.first { $0.id == pinned.sourceID }
                    HStack {
                        Image(systemName: source?.icon ?? "doc.text.magnifyingglass").font(.caption2).foregroundStyle(QuizzlerTheme.primaryCyan)
                        Text("[\(source?.title ?? "Source")] \(pinned.summary)").font(.caption2).foregroundStyle(QuizzlerTheme.textPrimary)
                        Spacer()
                        Button { pinnedItemIDs.remove(pinned.id) } label: {
                            Image(systemName: "xmark.circle.fill").font(.caption2).foregroundStyle(QuizzlerTheme.textMuted)
                        }
                        .buttonStyle(.plain)
                    }
                    .padding(5).background(QuizzlerTheme.raisedCard, in: RoundedRectangle(cornerRadius: 4))
                }
            }
        }
        .labCard()
    }

    private var scopeQuerySection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(data.scopeTitle, systemImage: "magnifyingglass").font(.subheadline.weight(.semibold)).foregroundStyle(QuizzlerTheme.textPrimary)

            ForEach(data.scopeQueries) { opt in
                Button { executedScopeQueryID = opt.id } label: {
                    HStack {
                        VStack(alignment: .leading, spacing: 1) {
                            Text(opt.query).font(QuizzlerTheme.metadataFont)
                                .foregroundStyle(executedScopeQueryID == opt.id ? QuizzlerTheme.primaryCyan : QuizzlerTheme.textPrimary)
                            Text(opt.label).font(.caption2).foregroundStyle(QuizzlerTheme.textMuted)
                        }
                        Spacer()
                        if executedScopeQueryID == opt.id { Image(systemName: "checkmark.circle.fill").foregroundStyle(QuizzlerTheme.primaryCyan) }
                    }
                    .padding(8).frame(maxWidth: .infinity, minHeight: QuizzlerTheme.minimumTouchTarget)
                    .background(QuizzlerTheme.raisedCard, in: RoundedRectangle(cornerRadius: 6))
                    .overlay(RoundedRectangle(cornerRadius: 6).stroke(executedScopeQueryID == opt.id ? QuizzlerTheme.primaryCyan : QuizzlerTheme.border, lineWidth: 1))
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("lab-query-\(opt.id)")
            }

            if let activeID = executedScopeQueryID, let option = data.scopeQueries.first(where: { $0.id == activeID }) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Result:").font(.caption2.weight(.bold)).foregroundStyle(QuizzlerTheme.primaryCyan)
                    Text(option.result).font(.footnote).foregroundStyle(QuizzlerTheme.textPrimary).fixedSize(horizontal: false, vertical: true)
                }
                .padding(8).frame(maxWidth: .infinity, alignment: .leading)
                .background(QuizzlerTheme.terminalBackground, in: RoundedRectangle(cornerRadius: 6))
            }
        }
        .labCard()
    }

    private var responseChoiceSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(data.responseTitle, systemImage: "bolt.shield").font(.subheadline.weight(.semibold)).foregroundStyle(QuizzlerTheme.textPrimary)

            ForEach(data.responses) { choice in
                let isSelected = selectedResponseID == choice.id
                Button { selectedResponseID = choice.id } label: {
                    VStack(alignment: .leading, spacing: 2) {
                        HStack {
                            Text(choice.title).font(.subheadline.weight(.semibold))
                                .foregroundStyle(isSelected ? QuizzlerTheme.primaryCyan : QuizzlerTheme.textPrimary)
                            Spacer()
                            if isSelected { Image(systemName: "checkmark.circle.fill").foregroundStyle(QuizzlerTheme.primaryCyan) }
                        }
                        Text(choice.consequenceSummary).font(.caption2).foregroundStyle(QuizzlerTheme.textMuted).fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(10).frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                    .background(isSelected ? QuizzlerTheme.raisedCard : QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: 6))
                    .overlay(RoundedRectangle(cornerRadius: 6).stroke(isSelected ? QuizzlerTheme.primaryCyan : QuizzlerTheme.border, lineWidth: 1))
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("lab-response-\(choice.id)")
            }
        }
        .labCard()
    }

    private var handoffNoteSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Analyst Handoff Note", systemImage: "square.and.pencil").font(.subheadline.weight(.semibold)).foregroundStyle(QuizzlerTheme.textPrimary)

            TextEditor(text: $handoffNote)
                .frame(minHeight: 90).padding(6).scrollContentBackground(.hidden)
                .background(QuizzlerTheme.terminalBackground, in: RoundedRectangle(cornerRadius: 6))
                .overlay(RoundedRectangle(cornerRadius: 6).stroke(QuizzlerTheme.border, lineWidth: 1))
                .font(.footnote).foregroundStyle(QuizzlerTheme.textPrimary)
                .accessibilityIdentifier("lab-handoff-note")
        }
        .labCard()
    }
}

// MARK: - Pinned Investigation Status Bar

/// Keeps the submission checklist and Submit button visible while the
/// investigation scrolls, so the requirement being met and the action it
/// unlocks never sit off screen (C16).
struct LabInvestigationStatusBar: View {
    let pinnedCategoryCount: Int
    let hasResponse: Bool
    let hasExecutedQuery: Bool
    let noteCharacterCount: Int
    let canSubmitHandoff: Bool
    let onSubmitHandoff: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: QuizzlerTheme.stackGap) {
            VStack(alignment: .leading, spacing: 3) {
                chk(label: "2+ source categories pinned (\(pinnedCategoryCount)/2)", met: pinnedCategoryCount >= 2)
                chk(label: "Response action chosen", met: hasResponse)
                chk(label: "Scope query executed", met: hasExecutedQuery)
                chk(label: "Substantive note (\(noteCharacterCount)/20 chars min)", met: noteCharacterCount >= 20)
            }

            Button(action: onSubmitHandoff) {
                HStack { Text("Submit handoff & view debrief"); Image(systemName: "arrow.right") }
                    .font(.body.weight(.semibold)).frame(maxWidth: .infinity, minHeight: 44)
            }
            .buttonStyle(.borderedProminent).tint(QuizzlerTheme.primaryCyan).foregroundStyle(.black)
            .disabled(!canSubmitHandoff).opacity(canSubmitHandoff ? 1.0 : 0.4)
            .accessibilityIdentifier("lab-submit-handoff")
        }
        .padding(.horizontal, QuizzlerTheme.pageGutter)
        .padding(.vertical, 10)
        .background(QuizzlerTheme.terminalBackground)
        .overlay(alignment: .top) {
            Rectangle().fill(QuizzlerTheme.border).frame(height: 1)
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("lab-investigation-bar")
    }

    private func chk(label: String, met: Bool) -> some View {
        HStack(spacing: 5) {
            Image(systemName: met ? "checkmark.circle.fill" : "circle").font(.caption2)
                .foregroundStyle(met ? QuizzlerTheme.success : QuizzlerTheme.textMuted)
            Text(label).font(.caption2).foregroundStyle(met ? QuizzlerTheme.textPrimary : QuizzlerTheme.textMuted)
        }
    }
}

// MARK: - Debrief View

struct LabDebriefView: View {
    let data: QuietPowerShellCase
    let selectedResponseID: String?
    let handoffNote: String
    let onReplay: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Investigation Debrief").font(.title3.weight(.bold)).foregroundStyle(QuizzlerTheme.textPrimary)

            card(title: data.debrief.evidenceTitle, icon: "list.bullet.rectangle", color: QuizzlerTheme.warning) {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(Array(data.debrief.evidencePoints.enumerated()), id: \.offset) { _, point in bullet(point) }
                }
            }

            card(title: data.debrief.scopeTitle, icon: "lock.shield", color: QuizzlerTheme.primaryCyan) {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(Array(data.debrief.scopePoints.enumerated()), id: \.offset) { _, point in bullet(point) }
                }
            }

            card(title: data.debrief.responseTitle, icon: "dial.low", color: QuizzlerTheme.textPrimary) {
                if let selectedResponseID,
                   let response = data.responses.first(where: { $0.id == selectedResponseID }),
                   let evaluation = data.debrief.responseEvaluations.first(where: { $0.responseID == selectedResponseID }) {
                    let color: Color = response.impact == "danger" ? QuizzlerTheme.danger : response.impact == "warning" ? QuizzlerTheme.warning : QuizzlerTheme.success
                    evalText(evaluation.heading, evaluation.explanation, color: color)
                } else {
                    Text("No response selected.").font(.caption).foregroundStyle(QuizzlerTheme.textMuted)
                }
            }

            card(title: data.debrief.distractorTitle, icon: "info.circle", color: QuizzlerTheme.textMuted) {
                Text(data.debrief.distractorSummary)
                    .font(.caption).foregroundStyle(QuizzlerTheme.textMuted).fixedSize(horizontal: false, vertical: true)
            }

            card(title: "Recorded Handoff Note", icon: "note.text", color: QuizzlerTheme.textPrimary) {
                Text(handoffNote).font(.footnote).foregroundStyle(QuizzlerTheme.textPrimary)
                    .padding(8).frame(maxWidth: .infinity, alignment: .leading)
                    .background(QuizzlerTheme.terminalBackground, in: RoundedRectangle(cornerRadius: 6))
            }

            Button(action: onReplay) {
                HStack { Image(systemName: "arrow.counterclockwise"); Text("Replay Investigation") }
                    .font(.body.weight(.semibold)).frame(maxWidth: .infinity, minHeight: 44)
            }
            .buttonStyle(.bordered).tint(QuizzlerTheme.primaryCyan)
            .accessibilityIdentifier("lab-replay-button")
        }
    }

    private func card<Content: View>(title: String, icon: String, color: Color, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Label(title, systemImage: icon).font(.subheadline.weight(.bold)).foregroundStyle(color)
            content()
        }
        .labCard()
    }

    private func evalText(_ heading: String, _ desc: String, color: Color) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(heading).font(.footnote.weight(.bold)).foregroundStyle(color)
            Text(desc).font(.caption).foregroundStyle(QuizzlerTheme.textMuted)
        }
    }

    private func bullet(_ text: String) -> some View {
        HStack(alignment: .top, spacing: 5) {
            Text("•").font(.caption.weight(.bold)).foregroundStyle(QuizzlerTheme.primaryCyan)
            Text(text).font(.caption).foregroundStyle(QuizzlerTheme.textMuted).fixedSize(horizontal: false, vertical: true)
        }
    }
}
