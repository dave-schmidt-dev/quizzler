import SwiftUI
import QuizzlerKit

// MARK: - Context

struct ReportQuestionContext: Equatable, Sendable {
    let identity: QuestionIdentity
    let qid: String
    let questionType: QuestionType
    let appVersion: String
    let build: String
    let selectedResponse: String?
    let prompt: String
    let options: [String]

    var type: String { questionType.rawValue }

    init(
        identity: QuestionIdentity,
        qid: String,
        questionType: QuestionType,
        appVersion: String,
        build: String,
        selectedResponse: String? = nil,
        prompt: String = "",
        options: [String] = []
    ) {
        self.identity = identity
        self.qid = qid
        self.questionType = questionType
        self.appVersion = appVersion
        self.build = build
        let trimmedResponse = selectedResponse?.trimmingCharacters(in: .whitespacesAndNewlines)
        self.selectedResponse = trimmedResponse?.isEmpty == true ? nil : trimmedResponse
        self.prompt = prompt
        self.options = options
    }
}

// MARK: - Chip model

/// Maps each selectable report category to its label, CloudKit category,
/// and the description string format defined by C5.
enum ReportChip: CaseIterable {
    case wrongAnswer
    case confusing
    case typo
    case other

    var label: String {
        switch self {
        case .wrongAnswer: "Marked answer is wrong"
        case .confusing:   "Confusing or ambiguous"
        case .typo:        "Typo or wording"
        case .other:       "Something else"
        }
    }

    var category: QuestionIssueCategory {
        switch self {
        case .wrongAnswer: .incorrectAnswer
        case .confusing:   .other
        case .typo:        .typo
        case .other:       .other
        }
    }

    var accessibilityIdentifier: String {
        switch self {
        case .wrongAnswer: "report-chip-wrong"
        case .confusing:   "report-chip-confusing"
        case .typo:        "report-chip-typo"
        case .other:       "report-chip-other"
        }
    }

    /// Produces the C5-specified description string:
    /// `"<label>[ · you think it's: <proposed>][: <trimmed detail>]"`
    /// The proposed option is only honoured for `.wrongAnswer`.
    /// The detail is only included when non-blank after trimming.
    static func description(chip: ReportChip, proposed: String?, detail: String) -> String {
        var result = chip.label
        let trimmedDetail = detail.trimmingCharacters(in: .whitespacesAndNewlines)
        if chip == .wrongAnswer, let proposed, !proposed.isEmpty {
            result += " · you think it's: \(proposed)"
        }
        if !trimmedDetail.isEmpty {
            result += ": \(trimmedDetail)"
        }
        return result
    }
}

// MARK: - View

/// The report preview deliberately contains no progress history or session data.
struct ReportQuestionView: View {
    let context: ReportQuestionContext
    let repository: any LaunchpadProgressRepository
    @Environment(\.dismiss) private var dismiss

    @State private var selectedChip: ReportChip?
    @State private var proposedOption: String?
    @State private var detail = ""
    @State private var queued = false
    @State private var saving = false
    @State private var saveFailed = false
    @State private var pendingIssueID: String?

    init(context: ReportQuestionContext, repository: any LaunchpadProgressRepository) {
        self.context = context
        self.repository = repository
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    // Quoted prompt card
                    if !context.prompt.isEmpty {
                        promptCard
                    }

                    // Category chips
                    VStack(spacing: 8) {
                        ForEach(ReportChip.allCases, id: \.self) { chip in
                            chipButton(chip)
                        }
                    }

                    // Proposed-answer picker (wrong-answer chip only, when options exist)
                    if selectedChip == .wrongAnswer && !context.options.isEmpty {
                        proposedPicker
                    }

                    // Optional detail field
                    TextField("Add a detail (optional)", text: $detail, axis: .vertical)
                        .textFieldStyle(.roundedBorder)
                        .lineLimit(2...6)
                        .accessibilityLabel("Optional report detail")

                    // Send button
                    Button {
                        sendReport()
                    } label: {
                        let label: String = {
                            if queued { return "Saved" }
                            if saveFailed { return "Retry sending" }
                            return "Send report"
                        }()
                        Text(label)
                            .font(.headline)
                            .frame(maxWidth: .infinity, minHeight: 48)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(QuizzlerTheme.primaryCyan)
                    .foregroundStyle(.black)
                    .frame(maxWidth: .infinity, minHeight: 48)
                    .disabled(!canSend)
                    .accessibilityIdentifier("report-send")

                    // Status feedback
                    if saving {
                        HStack(spacing: 8) {
                            ProgressView()
                                .controlSize(.small)
                            Text("Saving report…")
                        }
                        .font(.subheadline)
                        .foregroundStyle(QuizzlerTheme.textMuted)
                        .accessibilityElement(children: .combine)
                        .accessibilityLabel("Saving report")
                    } else if queued {
                        Text("Saved. Quizzler sends it with your next sync, and it is filed on your Mac for review.")
                            .font(.subheadline)
                            .foregroundStyle(QuizzlerTheme.textMuted)
                    } else if saveFailed {
                        Text("Report was not sent. Try again.")
                            .font(.subheadline)
                            .foregroundStyle(QuizzlerTheme.danger)
                    }
                }
                .padding(QuizzlerTheme.pageGutter)
            }
            .background(QuizzlerTheme.terminalBackground.ignoresSafeArea())
            .navigationTitle("Report question")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    // MARK: - Subviews

    private var promptCard: some View {
        Text(context.prompt)
            .font(QuizzlerTheme.readableFont)
            .foregroundStyle(QuizzlerTheme.textPrimary)
            .fixedSize(horizontal: false, vertical: true)
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
            .overlay(
                RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius)
                    .stroke(QuizzlerTheme.primaryCyan.opacity(0.25), lineWidth: 1)
            )
            .accessibilityLabel("Question: \(context.prompt)")
    }

    private func chipButton(_ chip: ReportChip) -> some View {
        let isSelected = selectedChip == chip
        return Button {
            if isSelected {
                selectedChip = nil
                proposedOption = nil
            } else {
                selectedChip = chip
                proposedOption = nil
            }
        } label: {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(isSelected ? QuizzlerTheme.primaryCyan : QuizzlerTheme.textMuted)
                    .accessibilityHidden(true)
                Text(chip.label)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(isSelected ? QuizzlerTheme.primaryCyan : QuizzlerTheme.textPrimary)
            }
            .padding(.horizontal, 12)
            .frame(maxWidth: .infinity, minHeight: QuizzlerTheme.minimumTouchTarget, alignment: .leading)
            .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
            .overlay(
                RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius)
                    .stroke(isSelected ? QuizzlerTheme.primaryCyan : QuizzlerTheme.border, lineWidth: isSelected ? 1.5 : 1)
            )
        }
        .accessibilityIdentifier(chip.accessibilityIdentifier)
        .accessibilityAddTraits(isSelected ? [.isSelected] : [])
    }

    private var proposedPicker: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("You think it's")
                .font(.caption)
                .foregroundStyle(QuizzlerTheme.textMuted)
            // Stacked, not flowed: pack options are often full sentences, and a
            // flow layout sizes each pill to one unwrapped line.
            VStack(alignment: .leading, spacing: 8) {
                ForEach(Array(context.options.enumerated()), id: \.offset) { index, option in
                    proposedPill(option: option, index: index)
                }
            }
        }
    }

    private func proposedPill(option: String, index: Int) -> some View {
        let isSelected = proposedOption == option
        return Button {
            proposedOption = isSelected ? nil : option
        } label: {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(isSelected ? QuizzlerTheme.primaryCyan : QuizzlerTheme.textMuted)
                    .accessibilityHidden(true)
                Text(option)
                    .font(.subheadline)
                    .foregroundStyle(isSelected ? QuizzlerTheme.primaryCyan : QuizzlerTheme.textPrimary)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity, minHeight: QuizzlerTheme.minimumTouchTarget, alignment: .leading)
            .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
            .overlay(
                RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius)
                    .stroke(isSelected ? QuizzlerTheme.primaryCyan : QuizzlerTheme.border, lineWidth: isSelected ? 1.5 : 1)
            )
        }
        .accessibilityIdentifier("report-proposed-\(index)")
        .accessibilityAddTraits(isSelected ? [.isSelected] : [])
    }

    // MARK: - Logic

    private var canSend: Bool {
        selectedChip != nil && !queued && !saving
    }

    private func sendReport() {
        guard let chip = selectedChip else { return }
        let issueID = pendingIssueID ?? "issue-\(UUID().uuidString.lowercased())"
        let descriptionText = ReportChip.description(chip: chip, proposed: proposedOption, detail: detail)
        guard let issue = try? QuestionIssue(
            issueID: issueID,
            courseID: context.identity.courseID,
            packID: context.identity.packID,
            questionID: context.identity.questionID,
            questionType: context.questionType,
            appVersion: context.appVersion,
            build: context.build,
            selectedResponse: context.selectedResponse,
            description: descriptionText
        ) else {
            saveFailed = true
            return
        }
        pendingIssueID = issueID
        saving = true
        Task { @MainActor in
            do {
                _ = try await repository.queueIssueAndScheduleSync(issue)
                queued = true
            } catch {
                saveFailed = true
            }
            saving = false
        }
    }
}
