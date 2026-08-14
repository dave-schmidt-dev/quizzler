import SwiftUI
import QuizzlerKit

struct ReportQuestionContext: Equatable, Sendable {
    let identity: QuestionIdentity
    let qid: String
    let questionType: QuestionType
    let course: String
    let appVersion: String
    let build: String
    let selectedResponse: String?

    var type: String { questionType.rawValue }

    init(
        identity: QuestionIdentity,
        qid: String,
        questionType: QuestionType,
        course: String,
        appVersion: String,
        build: String,
        selectedResponse: String? = nil
    ) {
        self.identity = identity
        self.qid = qid
        self.questionType = questionType
        self.course = course
        self.appVersion = appVersion
        self.build = build
        let trimmedResponse = selectedResponse?.trimmingCharacters(in: .whitespacesAndNewlines)
        self.selectedResponse = trimmedResponse?.isEmpty == true ? nil : trimmedResponse
    }
}

/// The report preview deliberately contains no progress history or session data.
struct ReportQuestionView: View {
    let context: ReportQuestionContext
    let repository: ProgressRepository
    @Environment(\.dismiss) private var dismiss
    @State private var category: QuestionIssueCategory = .other
    @State private var note = ""
    @State private var queued = false
    @State private var saving = false
    @State private var saveFailed = false
    @State private var pendingIssueID: String?

    private static let localRepository: ProgressRepository = {
        guard let applicationSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else {
            preconditionFailure("Application Support is unavailable")
        }
        let fileURL = applicationSupport
            .appendingPathComponent("Quizzler", isDirectory: true)
            .appendingPathComponent("progress-v1.json", isDirectory: false)
        return ProgressRepository(actorID: "local-device", store: LocalProgressStore(fileURL: fileURL))
    }()

    init(context: ReportQuestionContext, repository: ProgressRepository = ReportQuestionView.localRepository) {
        self.context = context
        self.repository = repository
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("Preview")
                        .font(.headline)
                        .foregroundStyle(QuizzlerTheme.textPrimary)
                    Text("Reports include question context only. Progress history is excluded.")
                        .font(.subheadline)
                        .foregroundStyle(QuizzlerTheme.textMuted)
                    contextCard
                    Picker("Issue type", selection: $category) {
                        ForEach(QuestionIssueCategory.allCases, id: \.self) { category in
                            Text(category.displayName).tag(category)
                        }
                    }
                    .pickerStyle(.menu)
                    .frame(minHeight: QuizzlerTheme.minimumTouchTarget)
                    TextField("Optional note", text: $note, axis: .vertical)
                        .textFieldStyle(.roundedBorder)
                        .lineLimit(3...6)
                        .accessibilityLabel("Optional report note")
                    Button {
                        let issueID = pendingIssueID ?? "issue-\(UUID().uuidString.lowercased())"
                        guard let issue = try? QuestionIssue(
                            issueID: issueID,
                            courseID: context.identity.courseID,
                            packID: context.identity.packID,
                            questionID: context.identity.questionID,
                            questionType: context.questionType,
                            appVersion: context.appVersion,
                            build: context.build,
                            selectedResponse: context.selectedResponse,
                            description: reportDescription
                        ) else {
                            saveFailed = true
                            return
                        }
                        pendingIssueID = issueID
                        saving = true
                        Task { @MainActor in
                            do {
                                _ = try await repository.queueIssue(issue)
                                queued = true
                            } catch {
                                saveFailed = true
                            }
                            saving = false
                        }
                    } label: {
                        Text(queued ? "Issue queued locally" : (saveFailed ? "Retry Queue Issue" : "Queue Issue"))
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(QuizzlerTheme.primaryCyan)
                    .foregroundStyle(.black)
                    .frame(maxWidth: .infinity, minHeight: 48)
                    .disabled(queued || saving)
                }
                .padding(QuizzlerTheme.pageGutter)
            }
            .background(QuizzlerTheme.terminalBackground.ignoresSafeArea())
            .navigationTitle("Report Question")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                        .frame(minWidth: QuizzlerTheme.minimumTouchTarget, minHeight: QuizzlerTheme.minimumTouchTarget)
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    private var contextCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            ContextRow(label: "QID", value: context.qid)
            ContextRow(label: "Type", value: context.type.replacingOccurrences(of: "_", with: " "))
            ContextRow(label: "Course", value: context.course)
            ContextRow(label: "App version", value: context.appVersion)
            ContextRow(label: "Build", value: context.build)
            if let selectedResponse = context.selectedResponse {
                ContextRow(label: "Selected response", value: selectedResponse)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(QuizzlerTheme.elevatedCard, in: RoundedRectangle(cornerRadius: QuizzlerTheme.cardRadius))
        .accessibilityElement(children: .combine)
    }

    private var reportDescription: String {
        let trimmed = note.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? category.displayName : "\(category.displayName): \(trimmed)"
    }
}

private struct ContextRow: View {
    let label: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label.uppercased())
                .font(QuizzlerTheme.metadataFont)
                .foregroundStyle(QuizzlerTheme.textMuted)
            Text(value)
                .font(QuizzlerTheme.readableFont)
                .foregroundStyle(QuizzlerTheme.textPrimary)
                .textSelection(.enabled)
        }
    }
}

private extension QuestionIssueCategory {
    var displayName: String {
        switch self {
        case .incorrectAnswer: "Incorrect answer"
        case .typo: "Typo or wording"
        case .brokenMedia: "Broken media"
        case .other: "Other"
        }
    }
}
