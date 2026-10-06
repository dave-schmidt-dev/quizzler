import Foundation
import XCTest
@testable import QuizzlerKit

final class ActiveSessionStoreTests: XCTestCase {

    // MARK: - Helpers

    private func temporaryDirectoryURL() -> URL {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("ActiveSessionStoreTests-\(UUID().uuidString)", isDirectory: true)
        addTeardownBlock {
            try? FileManager.default.removeItem(at: directory)
        }
        return directory
    }

    private func storeURL(in directory: URL) -> URL {
        directory.appendingPathComponent("active-session-v1.json", isDirectory: false)
    }

    private func session(
        questionID: String,
        courseID: String = "cissp",
        packID: String = "core",
        position: Int = 0
    ) -> PersistedSession {
        PersistedSession(
            courseID: courseID,
            packID: packID,
            packFingerprint: "sha256:fixed",
            mode: .normal,
            plan: [QuestionIdentity(courseID: courseID, packID: packID, questionID: questionID)],
            position: position
        )
    }

    // MARK: - Save, load, clear

    func testSaveLoadAndClearRoundTrip() {
        let store = ActiveSessionStore(fileURL: storeURL(in: temporaryDirectoryURL()))
        let saved = session(questionID: "q0", position: 1)

        XCTAssertNil(store.load(packKey: saved.packKey))
        store.save(saved)
        XCTAssertEqual(store.load(packKey: saved.packKey), saved)

        // Saving again for the same pack replaces, it never accumulates.
        let replacement = session(questionID: "q1", position: 0)
        store.save(replacement)
        XCTAssertEqual(store.load(packKey: saved.packKey), replacement)

        store.clear(packKey: saved.packKey)
        XCTAssertNil(store.load(packKey: saved.packKey))
    }

    func testPerPackIsolation() {
        let store = ActiveSessionStore(fileURL: storeURL(in: temporaryDirectoryURL()))
        let core = session(questionID: "q0", packID: "core")
        let drill = session(questionID: "q1", packID: "drill")

        store.save(core)
        store.save(drill)
        XCTAssertEqual(store.load(packKey: "cissp/core"), core)
        XCTAssertEqual(store.load(packKey: "cissp/drill"), drill)

        store.clear(packKey: "cissp/core")
        XCTAssertNil(store.load(packKey: "cissp/core"))
        XCTAssertEqual(store.load(packKey: "cissp/drill"), drill)
    }

    // MARK: - Failure modes

    func testMissingFileGivesNil() {
        let store = ActiveSessionStore(fileURL: storeURL(in: temporaryDirectoryURL()))
        XCTAssertNil(store.load(packKey: "cissp/core"))
    }

    func testCorruptFileGivesNilAndIsRemoved() throws {
        let directory = temporaryDirectoryURL()
        let fileURL = storeURL(in: directory)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try Data("not a session store".utf8).write(to: fileURL)

        let store = ActiveSessionStore(fileURL: fileURL)
        XCTAssertNil(store.load(packKey: "cissp/core"))
        XCTAssertFalse(FileManager.default.fileExists(atPath: fileURL.path))

        // The store keeps working after the corrupt file was swept away.
        let saved = session(questionID: "q0")
        store.save(saved)
        XCTAssertEqual(store.load(packKey: saved.packKey), saved)
    }
}
