import Foundation
import XCTest
@testable import QuizzlerKit

final class PackFingerprintTests: XCTestCase {

    // MARK: - Helpers

    private func makeQuestion(id: String, prompt: String? = nil) -> Question {
        .multipleChoice(MultipleChoiceQuestion(
            id: id,
            metadata: QuestionMetadata(topic: "Ops", examArea: "Security Operations", difficulty: .easy),
            prompt: prompt ?? "Prompt \(id)",
            explanation: "Explanation \(id)",
            options: ["A", "B"],
            answer: 0
        ))
    }

    private func makePack(questions: [Question]) throws -> InstalledPack {
        let manifest = try PackManifest(
            packID: "core",
            subject: "CISSP",
            title: "Core",
            questions: questions
        )
        return InstalledPack(courseID: "cissp", manifest: manifest)
    }

    // MARK: - Stability

    func testFingerprintIsStableAcrossCalls() throws {
        let pack = try makePack(questions: [makeQuestion(id: "q0"), makeQuestion(id: "q1")])
        XCTAssertEqual(PackFingerprint.make(for: pack), PackFingerprint.make(for: pack))
    }

    // MARK: - Content sensitivity

    func testFingerprintChangesWhenQuestionTextChanges() throws {
        let original = try makePack(questions: [makeQuestion(id: "q0", prompt: "Original prompt")])
        let edited = try makePack(questions: [makeQuestion(id: "q0", prompt: "Edited prompt")])
        XCTAssertNotEqual(PackFingerprint.make(for: original), PackFingerprint.make(for: edited))
    }

    func testFingerprintChangesWhenManifestVersionChanges() {
        // PackManifest pins its contract version to 1, so version sensitivity
        // is exercised through the component form of the same computation.
        let questions = [makeQuestion(id: "q0")]
        let version1 = PackFingerprint.make(courseID: "cissp", packID: "core", version: 1, questions: questions)
        let version2 = PackFingerprint.make(courseID: "cissp", packID: "core", version: 2, questions: questions)
        XCTAssertNotEqual(version1, version2)
    }

    func testFingerprintChangesWhenCourseOrPackChanges() throws {
        let questions = [makeQuestion(id: "q0")]
        let manifest = try PackManifest(
            packID: "core",
            subject: "CISSP",
            title: "Core",
            questions: questions
        )
        let pack = InstalledPack(courseID: "cissp", manifest: manifest)
        let otherCourse = InstalledPack(courseID: "ccna", manifest: manifest)
        XCTAssertNotEqual(PackFingerprint.make(for: pack), PackFingerprint.make(for: otherCourse))

        let otherPackManifest = try PackManifest(
            packID: "drill",
            subject: "CISSP",
            title: "Drill",
            questions: questions
        )
        let otherPack = InstalledPack(courseID: "cissp", manifest: otherPackManifest)
        XCTAssertNotEqual(PackFingerprint.make(for: pack), PackFingerprint.make(for: otherPack))
    }
}
