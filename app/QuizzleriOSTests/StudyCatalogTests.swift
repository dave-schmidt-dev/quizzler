import XCTest
@testable import QuizzleriOS
import QuizzlerKit

/// Covers the rule that replaced the hardcoded three-question array: the app
/// studies installed packs, and shows nothing when there are none.
@MainActor
final class StudyCatalogTests: XCTestCase {
    private func pack(courseID: String, packID: String, subject: String, questionCount: Int = 3) throws -> InstalledPack {
        let questions = try (0..<questionCount).map { index in
            Question.multipleChoice(MultipleChoiceQuestion(
                id: "q\(index)",
                metadata: QuestionMetadata(topic: "topic", examArea: "area", difficulty: .easy),
                prompt: "Prompt \(index)",
                explanation: "Explanation \(index)",
                options: ["A", "B"],
                answer: 0
            ))
        }
        let manifest = try PackManifest(packID: packID, subject: subject, title: "Core", questions: questions)
        return InstalledPack(courseID: courseID, manifest: manifest)
    }

    private func model(packs: [InstalledPack] = [], failures: [PackLoadFailure] = [], loadError: Error? = nil) -> StudyCatalogModel {
        StudyCatalogModel { (packs, failures, loadError) }
    }

    private func loaded(_ model: StudyCatalogModel) async throws -> StudyCatalogModel {
        model.loadPacks()
        for _ in 0..<100 {
            if model.state != .loading { return model }
            try await Task.sleep(nanoseconds: 5_000_000)
        }
        XCTFail("catalog never left the loading state")
        return model
    }

    func testAnInstalledPackBecomesTheStudyContent() async throws {
        let model = try await loaded(model(packs: [try pack(courseID: "cissp", packID: "cissp-core", subject: "CISSP", questionCount: 203)]))

        XCTAssertEqual(model.pack?.packID, "cissp-core")
        XCTAssertEqual(model.questions.count, 203)
        XCTAssertEqual(model.courseTitle, "CISSP")
        // Identity comes from the pack, so answers are attributed to the course
        // the user actually studied.
        XCTAssertEqual(model.questions.first?.identity.courseID, "cissp")
        XCTAssertEqual(model.questions.first?.identity.packID, "cissp-core")
    }

    func testNoInstalledPackLeavesTheAppWithoutQuestions() async throws {
        let model = try await loaded(model())

        XCTAssertNil(model.pack)
        XCTAssertTrue(model.questions.isEmpty)
        XCTAssertEqual(model.courseTitle, "No pack installed")
        guard case .unavailable(let reason) = model.state else { return XCTFail("expected .unavailable, got \(model.state)") }
        XCTAssertTrue(reason.contains("No question packs are installed"))
    }

    func testARefusedPackIsNamedInTheEmptyStateRatherThanHidden() async throws {
        let failure = PackLoadFailure(path: "cissp/cissp-core.json", reason: "content digest sha256:aa does not match")
        let model = try await loaded(model(failures: [failure]))

        guard case .unavailable(let reason) = model.state else { return XCTFail("expected .unavailable, got \(model.state)") }
        XCTAssertTrue(reason.contains("cissp/cissp-core.json"), reason)
        XCTAssertTrue(reason.contains("digest"), reason)
        XCTAssertEqual(model.failures, [failure])
    }

    func testAMissingAssetManifestSaysTheBuildLacksPackAssets() async throws {
        let model = try await loaded(model(loadError: PackCatalogError.manifestMissing))

        guard case .unavailable(let reason) = model.state else { return XCTFail("expected .unavailable, got \(model.state)") }
        XCTAssertTrue(reason.contains("no question assets"), reason)
    }

    func testARealCourseIsPreferredOverSamplesButSamplesStillRuns() async throws {
        let samples = try pack(courseID: "samples", packID: "samples-demo", subject: "Samples")
        let cissp = try pack(courseID: "cissp", packID: "cissp-core", subject: "CISSP")

        let both = try await loaded(model(packs: [samples, cissp]))
        XCTAssertEqual(both.pack?.courseID, "cissp")

        let samplesOnly = try await loaded(model(packs: [samples]))
        XCTAssertEqual(samplesOnly.pack?.courseID, "samples")
    }

    func testAGoodPackStillLoadsWhenAnotherIsRefused() async throws {
        let failure = PackLoadFailure(path: "broken/pack.json", reason: "file is not readable JSON")
        let model = try await loaded(model(packs: [try pack(courseID: "cissp", packID: "cissp-core", subject: "CISSP")], failures: [failure]))

        XCTAssertEqual(model.pack?.courseID, "cissp")
        // Still reported: Settings renders these so a missing course has a cause.
        XCTAssertEqual(model.failures, [failure])
    }

    func testStudyQuestionExposesThePackScopedIdentifierUsedByReports() throws {
        let installed = try pack(courseID: "cissp", packID: "cissp-core", subject: "CISSP", questionCount: 1)
        let question = try XCTUnwrap(installed.questions.first)
        let study = StudyQuestion(pack: installed, question: question)

        XCTAssertEqual(study.qid, "cissp-core::q0")
        XCTAssertEqual(study.courseTitle, "CISSP")
        XCTAssertEqual(study.identity, QuestionIdentity(courseID: "cissp", packID: "cissp-core", questionID: "q0"))
    }
}

/// Guards against the fabricated counters in `docs/WALKTHROUGH-2026-08-18.md`
/// finding 2 coming back as literals.
final class TodayCounterSourceTests: XCTestCase {
    private var launchpad: String {
        get throws {
            let url = URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .appendingPathComponent("QuizzleriOS/Launchpad/LaunchpadView.swift")
            return try String(contentsOf: url, encoding: .utf8)
        }
    }

    func testTheTodayCardHasNoHardcodedCounts() throws {
        let source = try launchpad
        XCTAssertFalse(source.contains("Question 1 of 12"))
        XCTAssertFalse(source.contains("\"3/12\""))
        XCTAssertTrue(source.contains("Question \\(questionNumber) of \\(questionCount)"))
        XCTAssertTrue(source.contains("\\(correct)/\\(answered)"))
    }

    func testTheCourseLabelIsNotACompiledInConstant() throws {
        let source = try launchpad
        XCTAssertFalse(source.contains("Today · Security+"))
        XCTAssertTrue(source.contains("Today · \\(courseTitle)"))
        // The Launchpad must not reach for the preview fixture at all; the
        // Release exclusion is a second lock, not the only one.
        XCTAssertFalse(source.contains("SeededStudyData"))
    }
}
