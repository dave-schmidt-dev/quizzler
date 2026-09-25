import Foundation
import SwiftUI
import QuizzlerKit
import UIKit

#if targetEnvironment(macCatalyst)
@MainActor
final class IssueInboxModel: ObservableObject {
    @Published private(set) var receivedCount: Int = 0
    @Published private(set) var lastSuccessfulCheckTime: Date?
    @Published private(set) var isCheckRunning: Bool = false
    @Published private(set) var lastFailureReason: String?

    private let reader: IssueInboxReader?

    init() {
        let destinationURL = Self.inboxFileURL()

        if let destinationURL,
           FileManager.default.fileExists(atPath: destinationURL.path),
           let data = try? Data(contentsOf: destinationURL),
           let document = try? JSONDecoder().decode(IssueInboxDocument.self, from: data) {
            self.receivedCount = document.issues.count
        }

        if !Self.isUITestingOrXCTest, let destinationURL {
            let source = CloudKitIssueInboxSource(containerIdentifier: "iCloud.com.zerodelta.quizzler.dev")
            self.reader = IssueInboxReader(source: source, fileURL: destinationURL)
        } else {
            self.reader = nil
        }
    }

    func refresh() {
        guard !isCheckRunning else { return }
        guard !Self.isUITestingOrXCTest else { return }
        guard let reader else { return }

        isCheckRunning = true
        Task {
            do {
                let summary = try await reader.refresh()
                self.receivedCount = summary.totalCount
                self.lastSuccessfulCheckTime = Date()
                self.lastFailureReason = nil
            } catch let error as IssueInboxSourceError {
                switch error {
                case .changeTokenExpired:
                    self.lastFailureReason = "Change token expired · please check again"
                case .zoneNotFound:
                    self.lastFailureReason = "Question reports zone not found"
                case .unreadableIssueRecord:
                    self.lastFailureReason = "A question report could not be read. Update Quizzler on this Mac, then check again."
                }
            } catch is DecodingError, is IssueInboxDocumentError {
                self.lastFailureReason = "Local question reports store is unreadable"
            } catch {
                self.lastFailureReason = "Could not sync question reports from CloudKit"
            }
            self.isCheckRunning = false
        }
    }

    private static func inboxFileURL() -> URL? {
        guard let applicationSupport = try? FileManager.default.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        ) else { return nil }
        return applicationSupport
            .appendingPathComponent("Quizzler", isDirectory: true)
            .appendingPathComponent("issue-inbox-v1.json", isDirectory: false)
    }

    private static var isUITestingOrXCTest: Bool {
#if DEBUG
        if UITestFixture.isRunningUnderXCTest
            || UITestFixture.usesLocalProgress
            || UITestFixture.cloudStatusScript(environment: ProcessInfo.processInfo.environment) != nil {
            return true
        }
#endif
        let env = ProcessInfo.processInfo.environment
        return env["XCTestConfigurationFilePath"] != nil
            || env["QUIZZLER_UI_TEST_FIXTURE"] == "enabled"
            || env["QUIZZLER_UI_TEST_LOCAL_PROGRESS"] == "enabled"
            || env["QUIZZLER_UI_TEST_CLOUD_STATUS"] != nil
    }
}
#endif

struct SettingsView: View {
    @ObservedObject var catalog: StudyCatalogModel
    @ObservedObject var progress: LaunchpadProgressModel
    @AppStorage(StudySessionLength.key) private var storedSessionLength = StudySessionLength.default
    @AppStorage(StudyScheduledReview.key) private var scheduledReviewEnabled = StudyScheduledReview.default
    @State private var reviewExplanationPresented = false
#if targetEnvironment(macCatalyst)
    @ObservedObject var issueInbox: IssueInboxModel
#endif

    var body: some View {
        Form {
            Section("Study") {
                Picker("Default session limit", selection: $storedSessionLength) {
                    ForEach(StudySessionLength.options, id: \.self) { option in
                        Text(StudySessionLength.label(option))
                            .tag(option)
                    }
                }
                .accessibilityIdentifier("settings-default-session-limit")
                Text("Maximum questions for each new session. Today can choose a different limit once.")
                    .font(.caption)
                    .foregroundStyle(QuizzlerTheme.textMuted)

                Toggle("Offer scheduled reviews", isOn: $scheduledReviewEnabled)
                    .tint(QuizzlerTheme.primaryCyan)
                    .accessibilityIdentifier("settings-scheduled-review")
                Text("Spaced repetition of previously seen questions.")
                    .font(.caption)
                    .foregroundStyle(QuizzlerTheme.textMuted)

                Picker("Maximum Leitner level", selection: maximumLevelSelection) {
                    ForEach(1...7, id: \.self) { level in
                        Text("Level \(level) · \(LeitnerSchedule.intervalLabel(for: level))")
                            .tag(level)
                    }
                }
                .accessibilityIdentifier("settings-maximum-leitner-level")
                Text("Correct answers stop at this level. Lowering the maximum brings longer review dates forward.")
                    .font(.caption)
                    .foregroundStyle(QuizzlerTheme.textMuted)
                if let maximumLevelError = progress.maximumLevelError {
                    Text(maximumLevelError)
                        .font(.caption)
                        .foregroundStyle(QuizzlerTheme.danger)
                        .accessibilityIdentifier("settings-maximum-leitner-error")
                }

                Button("How scheduled reviews work") {
                    reviewExplanationPresented = true
                }
                .foregroundStyle(QuizzlerTheme.primaryCyan)
                .accessibilityIdentifier("settings-how-reviews-work")
            }
            if !catalog.failures.isEmpty {
                // A pack that was bundled but refused is reported here rather
                // than dropped, so the course going missing has a stated cause.
                Section("Packs not loaded") {
                    ForEach(catalog.failures, id: \.path) { failure in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(failure.path).font(.subheadline.weight(.semibold))
                            Text(failure.reason).font(.caption).foregroundStyle(QuizzlerTheme.textMuted)
                        }
                    }
                }
            }
#if targetEnvironment(macCatalyst)
            Section("Question reports") {
                LabeledContent("Received", value: "\(issueInbox.receivedCount)")
                    .accessibilityIdentifier("issue-inbox-received")
                LabeledContent("Last checked", value: lastCheckedDescription)
                    .accessibilityIdentifier("issue-inbox-last-checked")
                HStack(spacing: 8) {
                    Button("Check now") {
                        issueInbox.refresh()
                    }
                    .foregroundStyle(QuizzlerTheme.primaryCyan)
                    .disabled(issueInbox.isCheckRunning)
                    .accessibilityIdentifier("issue-inbox-check-now")

                    if issueInbox.isCheckRunning {
                        ProgressView()
                            .controlSize(.small)
                    }
                }
                if let failure = issueInbox.lastFailureReason {
                    Text(failure)
                        .font(.caption)
                        .foregroundStyle(QuizzlerTheme.danger)
                        .lineLimit(1)
                        .accessibilityIdentifier("issue-inbox-error")
                }
            }
#endif
            Section("About") {
                LabeledContent {
                    Text(NativeAppVersion.display)
                } label: {
                    Text("App version")
                }
                .accessibilityIdentifier("settings-app-version")
                Text("Question packs and your selected course stay on this device. Progress syncs through your iCloud account. Reports include question context only.")
            }
        }
        .scrollContentBackground(.hidden)
        .background(QuizzlerTheme.terminalBackground)
        .foregroundStyle(QuizzlerTheme.textPrimary)
        .sheet(isPresented: $reviewExplanationPresented) {
            ScheduledReviewsExplanationView(maximumLevel: progress.maximumLeitnerLevel)
        }
    }

    private var maximumLevelSelection: Binding<Int> {
        Binding(
            get: { progress.maximumLeitnerLevel },
            set: { progress.setMaximumLeitnerLevel($0) }
        )
    }

#if targetEnvironment(macCatalyst)
    private var lastCheckedDescription: String {
        guard let lastCheckTime = issueInbox.lastSuccessfulCheckTime else {
            return "Not yet"
        }
        let formatter = RelativeDateTimeFormatter()
        return formatter.localizedString(for: lastCheckTime, relativeTo: Date())
    }
#endif

}
