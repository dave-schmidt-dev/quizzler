import Foundation
import XCTest
@testable import QuizzlerKit

#if canImport(CloudKit)
import CloudKit
#endif

// MARK: - Test Fake Source

private actor FakeIssueInboxSource: IssueInboxSource {
    private var _capturedTokens: [Data?] = []
    private var _responses: [@Sendable (Data?) throws -> IssueInboxPage] = []

    var capturedTokens: [Data?] {
        _capturedTokens
    }

    func enqueueResponse(_ handler: @escaping @Sendable (Data?) throws -> IssueInboxPage) {
        _responses.append(handler)
    }

    func enqueuePage(_ page: IssueInboxPage) {
        enqueueResponse { _ in page }
    }

    func enqueueError(_ error: any Error & Sendable) {
        enqueueResponse { _ in throw error }
    }

    func fetchChanges(since token: Data?) async throws -> IssueInboxPage {
        _capturedTokens.append(token)
        guard !_responses.isEmpty else {
            fatalError("No scripted responses in FakeIssueInboxSource")
        }
        let handler = _responses.removeFirst()
        return try handler(token)
    }
}

// MARK: - IssueInboxTests

final class IssueInboxTests: XCTestCase {

    private func makeTemporaryFileURL() -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent("IssueInboxTests-\(UUID().uuidString)")
            .appendingPathComponent("issue-inbox-v1.json")
    }

    private func makeTestIssue(
        id: String = "issue-00000000-0000-4000-8000-000000000001",
        courseID: String = "cysa-plus",
        packID: String = "cysa-plus-core",
        questionID: String = "so005",
        questionType: QuestionType = .multipleChoice,
        appVersion: String = "1.0.0",
        build: String = "30",
        selectedResponse: String? = "Option B",
        description: String = "Test issue description"
    ) throws -> QuestionIssue {
        try QuestionIssue(
            issueID: id,
            courseID: courseID,
            packID: packID,
            questionID: questionID,
            questionType: questionType,
            appVersion: appVersion,
            build: build,
            selectedResponse: selectedResponse,
            description: description
        )
    }

    // MARK: - 1. Golden Fixture Test

    func testGoldenFixtureDecodesAndEncodesToIdenticalObject() throws {
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let fixtureURL = root.appendingPathComponent("protocol-fixtures/issue-inbox-v1.json")
        let fixtureData = try Data(contentsOf: fixtureURL)

        let document = try JSONDecoder().decode(IssueInboxDocument.self, from: fixtureData)
        XCTAssertNil(document.changeToken)
        XCTAssertEqual(document.issues.count, 2)

        let entry1 = try XCTUnwrap(document.issues["issue-00000000-0000-4000-8000-000000000001"])
        XCTAssertEqual(entry1.reportedAt, Date(timeIntervalSince1970: 1800000000))
        XCTAssertEqual(entry1.receivedAt, Date(timeIntervalSince1970: 1800000060))
        XCTAssertEqual(entry1.issue.issueID, "issue-00000000-0000-4000-8000-000000000001")
        XCTAssertEqual(entry1.issue.courseID, "cysa-plus")
        XCTAssertEqual(entry1.issue.packID, "cysa-plus-core")
        XCTAssertEqual(entry1.issue.questionID, "so005")
        XCTAssertEqual(entry1.issue.questionType, .multipleChoice)
        XCTAssertEqual(entry1.issue.appVersion, "1.0.0")
        XCTAssertEqual(entry1.issue.build, "30")
        XCTAssertEqual(entry1.issue.selectedResponse, "Option B")
        XCTAssertEqual(entry1.issue.description, "Fixture report: the keyed answer looks wrong.")

        let entry2 = try XCTUnwrap(document.issues["issue-00000000-0000-4000-8000-000000000002"])
        XCTAssertEqual(entry2.reportedAt, Date(timeIntervalSince1970: 1800000120))
        XCTAssertEqual(entry2.receivedAt, Date(timeIntervalSince1970: 1800000180))
        XCTAssertEqual(entry2.issue.issueID, "issue-00000000-0000-4000-8000-000000000002")
        XCTAssertEqual(entry2.issue.courseID, "it540")
        XCTAssertEqual(entry2.issue.packID, "it540-midterm-review-mod1-7")
        XCTAssertEqual(entry2.issue.questionID, "s13")
        XCTAssertEqual(entry2.issue.questionType, .multipleSelect)
        XCTAssertEqual(entry2.issue.appVersion, "1.0.0")
        XCTAssertEqual(entry2.issue.build, "30")
        XCTAssertNil(entry2.issue.selectedResponse)
        XCTAssertEqual(entry2.issue.description, "Fixture report with no selected response.")

        // Round-trip encoding
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .prettyPrinted]
        let encodedData = try encoder.encode(document)

        let fixtureObject = try XCTUnwrap(JSONSerialization.jsonObject(with: fixtureData) as? NSDictionary)
        let encodedObject = try XCTUnwrap(JSONSerialization.jsonObject(with: encodedData) as? NSDictionary)
        XCTAssertEqual(encodedObject, fixtureObject)

        // Decoding validation rejects wrong protocol
        var badProtocolDict = try XCTUnwrap(JSONSerialization.jsonObject(with: fixtureData) as? [String: Any])
        badProtocolDict["protocol"] = "wrong-protocol"
        let badProtocolData = try JSONSerialization.data(withJSONObject: badProtocolDict)
        XCTAssertThrowsError(try JSONDecoder().decode(IssueInboxDocument.self, from: badProtocolData)) { error in
            XCTAssertEqual(error as? IssueInboxDocumentError, .invalidProtocol("wrong-protocol"))
        }

        // Decoding validation rejects wrong version
        var badVersionDict = try XCTUnwrap(JSONSerialization.jsonObject(with: fixtureData) as? [String: Any])
        badVersionDict["version"] = 2
        let badVersionData = try JSONSerialization.data(withJSONObject: badVersionDict)
        XCTAssertThrowsError(try JSONDecoder().decode(IssueInboxDocument.self, from: badVersionData)) { error in
            XCTAssertEqual(error as? IssueInboxDocumentError, .unsupportedVersion(2))
        }

        // Decoding validation rejects key mismatch
        var badKeyDict = try XCTUnwrap(JSONSerialization.jsonObject(with: fixtureData) as? [String: Any])
        var issuesDict = try XCTUnwrap(badKeyDict["issues"] as? [String: Any])
        let value = issuesDict.removeValue(forKey: "issue-00000000-0000-4000-8000-000000000001")!
        issuesDict["mismatched-key"] = value
        badKeyDict["issues"] = issuesDict
        let badKeyData = try JSONSerialization.data(withJSONObject: badKeyDict)
        XCTAssertThrowsError(try JSONDecoder().decode(IssueInboxDocument.self, from: badKeyData)) { error in
            XCTAssertEqual(
                error as? IssueInboxDocumentError,
                .keyMismatch(key: "mismatched-key", issueID: "issue-00000000-0000-4000-8000-000000000001")
            )
        }
    }

    // MARK: - 2. Paging Across 3 Pages With Failure On Page 3

    func testPagingAcrossThreePagesWithFailureOnPageThree() async throws {
        let fileURL = makeTemporaryFileURL()
        defer { try? FileManager.default.removeItem(at: fileURL.deletingLastPathComponent()) }

        let fakeSource = FakeIssueInboxSource()

        let issue1 = try makeTestIssue(id: "issue-page-1")
        let rec1 = IssueInboxRecord(
            record: try CloudKitMapping.issueRecord(issue1),
            serverCreationDate: Date(timeIntervalSince1970: 1000)
        )
        await fakeSource.enqueuePage(IssueInboxPage(
            records: [rec1],
            changeToken: Data("token-page-1".utf8),
            moreComing: true
        ))

        let issue2 = try makeTestIssue(id: "issue-page-2")
        let rec2 = IssueInboxRecord(
            record: try CloudKitMapping.issueRecord(issue2),
            serverCreationDate: Date(timeIntervalSince1970: 2000)
        )
        await fakeSource.enqueuePage(IssueInboxPage(
            records: [rec2],
            changeToken: Data("token-page-2".utf8),
            moreComing: true
        ))

        struct SimulatedNetworkError: Error, Equatable, Sendable {}
        await fakeSource.enqueueError(SimulatedNetworkError())

        let reader = IssueInboxReader(source: fakeSource, fileURL: fileURL)
        do {
            _ = try await reader.refresh()
            XCTFail("Expected refresh to throw on page 3")
        } catch {
            // Expected
        }

        // Verify captured tokens: first call nil, second call token 1, third call token 2
        let captured = await fakeSource.capturedTokens
        XCTAssertEqual(captured.count, 3)
        XCTAssertNil(captured[0])
        XCTAssertEqual(captured[1], Data("token-page-1".utf8))
        XCTAssertEqual(captured[2], Data("token-page-2".utf8))

        // Verify the file was written after page 2 and holds pages 1-2 and page 2's token
        let savedData = try Data(contentsOf: fileURL)
        let savedDocument = try JSONDecoder().decode(IssueInboxDocument.self, from: savedData)
        XCTAssertEqual(savedDocument.changeToken, Data("token-page-2".utf8))
        XCTAssertEqual(savedDocument.issues.count, 2)
        XCTAssertNotNil(savedDocument.issues["issue-page-1"])
        XCTAssertNotNil(savedDocument.issues["issue-page-2"])
    }

    // MARK: - 3. Duplicate IDs Across Two Refreshes

    func testDuplicateIDsAcrossTwoRefreshesKeepsFirstPayload() async throws {
        let fileURL = makeTemporaryFileURL()
        defer { try? FileManager.default.removeItem(at: fileURL.deletingLastPathComponent()) }

        let fakeSource = FakeIssueInboxSource()

        let issueAFirst = try makeTestIssue(id: "issue-dup", description: "Original first sighting payload")
        await fakeSource.enqueuePage(IssueInboxPage(
            records: [IssueInboxRecord(record: try CloudKitMapping.issueRecord(issueAFirst))],
            changeToken: Data("token-run-1".utf8),
            moreComing: false
        ))

        let reader = IssueInboxReader(source: fakeSource, fileURL: fileURL)
        let summary1 = try await reader.refresh()
        XCTAssertEqual(summary1.newCount, 1)
        XCTAssertEqual(summary1.totalCount, 1)

        // Second refresh: same id with a different payload, plus a new issue B
        let issueASecond = try makeTestIssue(id: "issue-dup", description: "Modified second payload (must be ignored)")
        let issueB = try makeTestIssue(id: "issue-new", description: "Brand new issue")
        await fakeSource.enqueuePage(IssueInboxPage(
            records: [
                IssueInboxRecord(record: try CloudKitMapping.issueRecord(issueASecond)),
                IssueInboxRecord(record: try CloudKitMapping.issueRecord(issueB))
            ],
            changeToken: Data("token-run-2".utf8),
            moreComing: false
        ))

        let summary2 = try await reader.refresh()
        XCTAssertEqual(summary2.newCount, 1) // Only issueB is new
        XCTAssertEqual(summary2.totalCount, 2)

        let savedData = try Data(contentsOf: fileURL)
        let savedDoc = try JSONDecoder().decode(IssueInboxDocument.self, from: savedData)
        XCTAssertEqual(savedDoc.issues["issue-dup"]?.issue.description, "Original first sighting payload")
        XCTAssertEqual(savedDoc.issues["issue-new"]?.issue.description, "Brand new issue")
    }

    // MARK: - 4. Non-Issue Records Skipped and Counted

    func testNonIssueRecordsAreSkippedAndTokenAdvances() async throws {
        let fileURL = makeTemporaryFileURL()
        defer { try? FileManager.default.removeItem(at: fileURL.deletingLastPathComponent()) }

        let fakeSource = FakeIssueInboxSource()

        // 1. Non-issue: snapshot kind
        let snapshotRecord = try CloudKitMappedRecord(
            kind: .snapshot,
            recordName: "ProgressSnapshot/current",
            fields: [:]
        )
        // 2. Non-issue: operation kind
        let operationRecord = try CloudKitMappedRecord(
            kind: .operation,
            recordName: "ProgressOperation/op1",
            fields: [:]
        )
        // 3. Valid issue
        let validIssue = try makeTestIssue(id: "issue-valid")
        let validRecord = try CloudKitMapping.issueRecord(validIssue)

        await fakeSource.enqueuePage(IssueInboxPage(
            records: [
                IssueInboxRecord(record: snapshotRecord),
                IssueInboxRecord(record: operationRecord),
                IssueInboxRecord(record: validRecord)
            ],
            changeToken: Data("token-clean".utf8),
            moreComing: false
        ))

        nonisolated(unsafe) var receivedPageStatus: (fetched: Int, new: Int)?
        let reader = IssueInboxReader(
            source: fakeSource,
            fileURL: fileURL,
            onStatus: { status in
                if case let .page(fetched, new) = status {
                    receivedPageStatus = (fetched, new)
                }
            }
        )

        let summary = try await reader.refresh()
        XCTAssertEqual(summary.newCount, 1)
        XCTAssertEqual(summary.totalCount, 1)
        XCTAssertEqual(summary.skippedCount, 2)
        XCTAssertEqual(receivedPageStatus?.fetched, 3)
        XCTAssertEqual(receivedPageStatus?.new, 1)

        let savedData = try Data(contentsOf: fileURL)
        let savedDoc = try JSONDecoder().decode(IssueInboxDocument.self, from: savedData)
        XCTAssertEqual(savedDoc.changeToken, Data("token-clean".utf8))
        XCTAssertEqual(savedDoc.issues.count, 1)
        XCTAssertNotNil(savedDoc.issues["issue-valid"])
    }

    // MARK: - 5. Expired Token Restarts Once and Keeps Entries; Second Expiry Throws

    func testExpiredTokenRestartsFromNilOnceAndSecondExpiryThrows() async throws {
        let fileURL = makeTemporaryFileURL()
        defer { try? FileManager.default.removeItem(at: fileURL.deletingLastPathComponent()) }

        // Setup existing document with a stored token and 1 issue
        let existingIssue = try makeTestIssue(id: "issue-existing")
        let existingDoc = IssueInboxDocument(
            changeToken: Data("expired-token-1".utf8),
            issues: ["issue-existing": IssueInboxEntry(
                issue: existingIssue,
                reportedAt: Date(timeIntervalSince1970: 500),
                receivedAt: Date(timeIntervalSince1970: 500)
            )]
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .prettyPrinted]
        try FileManager.default.createDirectory(at: fileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try encoder.encode(existingDoc).write(to: fileURL)

        // Part A: First expiry restarts from nil and keeps existing entries
        let fakeSource1 = FakeIssueInboxSource()
        await fakeSource1.enqueueError(IssueInboxSourceError.changeTokenExpired)

        let newIssue = try makeTestIssue(id: "issue-after-restart")
        await fakeSource1.enqueuePage(IssueInboxPage(
            records: [IssueInboxRecord(record: try CloudKitMapping.issueRecord(newIssue))],
            changeToken: Data("fresh-token".utf8),
            moreComing: false
        ))

        let reader1 = IssueInboxReader(source: fakeSource1, fileURL: fileURL)
        let summary1 = try await reader1.refresh()
        XCTAssertEqual(summary1.newCount, 1)
        XCTAssertEqual(summary1.totalCount, 2)

        let captured = await fakeSource1.capturedTokens
        XCTAssertEqual(captured.count, 2)
        XCTAssertEqual(captured[0], Data("expired-token-1".utf8))
        XCTAssertNil(captured[1]) // Restarted from nil!

        let savedDoc = try JSONDecoder().decode(IssueInboxDocument.self, from: Data(contentsOf: fileURL))
        XCTAssertEqual(savedDoc.changeToken, Data("fresh-token".utf8))
        XCTAssertNotNil(savedDoc.issues["issue-existing"])
        XCTAssertNotNil(savedDoc.issues["issue-after-restart"])

        // Part B: Second expiry in same refresh throws
        let fakeSource2 = FakeIssueInboxSource()
        await fakeSource2.enqueueError(IssueInboxSourceError.changeTokenExpired)
        await fakeSource2.enqueueError(IssueInboxSourceError.changeTokenExpired)

        nonisolated(unsafe) var finalStatus: IssueInboxStatus?
        let reader2 = IssueInboxReader(
            source: fakeSource2,
            fileURL: fileURL,
            onStatus: { finalStatus = $0 }
        )

        do {
            _ = try await reader2.refresh()
            XCTFail("Expected second changeTokenExpired to throw")
        } catch {
            XCTAssertEqual(error as? IssueInboxSourceError, .changeTokenExpired)
        }
        XCTAssertEqual(finalStatus, .failed(.tokenExpired))
    }

    // MARK: - 6. Corrupt Existing File Throws and Leaves File Bytes Unchanged

    func testCorruptExistingFileThrowsAndLeavesBytesUnchanged() async throws {
        let fileURL = makeTemporaryFileURL()
        defer { try? FileManager.default.removeItem(at: fileURL.deletingLastPathComponent()) }

        try FileManager.default.createDirectory(at: fileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        let corruptBytes = Data("{\"protocol\": invalid json...".utf8)
        try corruptBytes.write(to: fileURL)

        let fakeSource = FakeIssueInboxSource()
        nonisolated(unsafe) var reportedStatuses: [IssueInboxStatus] = []
        let reader = IssueInboxReader(
            source: fakeSource,
            fileURL: fileURL,
            onStatus: { reportedStatuses.append($0) }
        )

        do {
            _ = try await reader.refresh()
            XCTFail("Expected refresh to throw on corrupt file")
        } catch {
            // Expected
        }

        // Assert file bytes are completely unchanged
        let currentBytes = try Data(contentsOf: fileURL)
        XCTAssertEqual(currentBytes, corruptBytes)

        // Assert status events: started, then failed(.unreadableStore)
        XCTAssertEqual(reportedStatuses, [.started, .failed(.unreadableStore)])
        let captured = await fakeSource.capturedTokens
        XCTAssertTrue(captured.isEmpty) // Source was not even called
    }

    // MARK: - 7. reportedAt Falls Back to now() When Creation Date Is Nil

    func testReportedAtFallsBackToNowWhenCreationDateIsNil() async throws {
        let fileURL = makeTemporaryFileURL()
        defer { try? FileManager.default.removeItem(at: fileURL.deletingLastPathComponent()) }

        let fakeSource = FakeIssueInboxSource()
        let issue = try makeTestIssue(id: "issue-no-creation-date")
        let recordWithoutDate = IssueInboxRecord(
            record: try CloudKitMapping.issueRecord(issue),
            serverCreationDate: nil
        )

        await fakeSource.enqueuePage(IssueInboxPage(
            records: [recordWithoutDate],
            changeToken: Data("tok".utf8),
            moreComing: false
        ))

        let fixedDate = Date(timeIntervalSince1970: 1715000000)
        let reader = IssueInboxReader(
            source: fakeSource,
            fileURL: fileURL,
            now: { fixedDate }
        )

        _ = try await reader.refresh()

        let savedDoc = try JSONDecoder().decode(IssueInboxDocument.self, from: Data(contentsOf: fileURL))
        let entry = try XCTUnwrap(savedDoc.issues["issue-no-creation-date"])
        XCTAssertEqual(entry.reportedAt, fixedDate)
        XCTAssertEqual(entry.receivedAt, fixedDate)
    }

    // MARK: - 8. Status Events Arrive In Order and .failed Precedes a Throw

    func testStatusEventsArriveInOrderAndFailedPrecedesThrow() async throws {
        let fileURL = makeTemporaryFileURL()
        defer { try? FileManager.default.removeItem(at: fileURL.deletingLastPathComponent()) }

        // Test 8A: Successful flow order
        let fakeSource1 = FakeIssueInboxSource()
        let issue = try makeTestIssue(id: "issue-status")
        await fakeSource1.enqueuePage(IssueInboxPage(
            records: [IssueInboxRecord(record: try CloudKitMapping.issueRecord(issue))],
            changeToken: Data("tok".utf8),
            moreComing: false
        ))

        nonisolated(unsafe) var statuses1: [IssueInboxStatus] = []
        let reader1 = IssueInboxReader(
            source: fakeSource1,
            fileURL: fileURL,
            onStatus: { statuses1.append($0) }
        )

        let summary = try await reader1.refresh()
        XCTAssertEqual(statuses1.count, 3)
        XCTAssertEqual(statuses1[0], .started)
        XCTAssertEqual(statuses1[1], .page(fetched: 1, new: 1))
        XCTAssertEqual(statuses1[2], .finished(summary))

        // Test 8B: Failure flow (.failed precedes throw)
        let fakeSource2 = FakeIssueInboxSource()
        await fakeSource2.enqueueError(IssueInboxSourceError.zoneNotFound)

        nonisolated(unsafe) var statuses2: [IssueInboxStatus] = []
        var threwError = false
        nonisolated(unsafe) var lastStatusBeforeCatch: IssueInboxStatus?

        let reader2 = IssueInboxReader(
            source: fakeSource2,
            fileURL: fileURL,
            onStatus: { status in
                statuses2.append(status)
                lastStatusBeforeCatch = status
            }
        )

        do {
            _ = try await reader2.refresh()
        } catch {
            threwError = true
            XCTAssertEqual(lastStatusBeforeCatch, .failed(.zoneNotFound))
            XCTAssertEqual(error as? IssueInboxSourceError, .zoneNotFound)
        }

        XCTAssertTrue(threwError)
        XCTAssertEqual(statuses2, [.started, .failed(.zoneNotFound)])
    }

    // MARK: - 9. Page With Unreadable Issue Record Names Keeps Previous Token and Emits Failure

    func testPageWithUnreadableIssueRecordNamesStoresReadableIssuesKeepsPreviousTokenAndThrows() async throws {
        let fileURL = makeTemporaryFileURL()
        defer { try? FileManager.default.removeItem(at: fileURL.deletingLastPathComponent()) }

        let existingIssue = try makeTestIssue(id: "issue-initial")
        let initialDoc = IssueInboxDocument(
            changeToken: Data("token-initial".utf8),
            issues: ["issue-initial": IssueInboxEntry(
                issue: existingIssue,
                reportedAt: Date(timeIntervalSince1970: 100),
                receivedAt: Date(timeIntervalSince1970: 100)
            )]
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .prettyPrinted]
        try FileManager.default.createDirectory(at: fileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try encoder.encode(initialDoc).write(to: fileURL)

        let fakeSource = FakeIssueInboxSource()

        let issue1 = try makeTestIssue(id: "issue-readable-1")
        let rec1 = IssueInboxRecord(
            record: try CloudKitMapping.issueRecord(issue1),
            serverCreationDate: Date(timeIntervalSince1970: 1000)
        )

        // First page has one readable issue and one unreadable issue record name
        await fakeSource.enqueuePage(IssueInboxPage(
            records: [rec1],
            unreadableIssueRecordNames: ["QuestionIssue/corrupted-1"],
            changeToken: Data("token-page-1".utf8),
            moreComing: false
        ))

        nonisolated(unsafe) var emittedStatuses: [IssueInboxStatus] = []
        let reader = IssueInboxReader(
            source: fakeSource,
            fileURL: fileURL,
            onStatus: { emittedStatuses.append($0) }
        )

        do {
            _ = try await reader.refresh()
            XCTFail("Expected refresh to throw unreadableIssueRecord")
        } catch {
            XCTAssertEqual(error as? IssueInboxSourceError, .unreadableIssueRecord)
        }

        XCTAssertTrue(emittedStatuses.contains(.failed(.unreadableIssueRecord)))

        // Verify readable issue was stored, but change token remains the previous one
        let savedData1 = try Data(contentsOf: fileURL)
        let savedDoc1 = try JSONDecoder().decode(IssueInboxDocument.self, from: savedData1)
        XCTAssertEqual(savedDoc1.changeToken, Data("token-initial".utf8))
        XCTAssertNotNil(savedDoc1.issues["issue-initial"])
        XCTAssertNotNil(savedDoc1.issues["issue-readable-1"])

        // Second refresh with clean page advances the token
        let issue2 = try makeTestIssue(id: "issue-readable-2")
        let rec2 = IssueInboxRecord(
            record: try CloudKitMapping.issueRecord(issue2),
            serverCreationDate: Date(timeIntervalSince1970: 2000)
        )
        await fakeSource.enqueuePage(IssueInboxPage(
            records: [rec2],
            unreadableIssueRecordNames: [],
            changeToken: Data("token-page-2-clean".utf8),
            moreComing: false
        ))

        let summary2 = try await reader.refresh()
        XCTAssertEqual(summary2.newCount, 1)
        XCTAssertEqual(summary2.totalCount, 3)

        let savedData2 = try Data(contentsOf: fileURL)
        let savedDoc2 = try JSONDecoder().decode(IssueInboxDocument.self, from: savedData2)
        XCTAssertEqual(savedDoc2.changeToken, Data("token-page-2-clean".utf8))
        XCTAssertNotNil(savedDoc2.issues["issue-initial"])
        XCTAssertNotNil(savedDoc2.issues["issue-readable-1"])
        XCTAssertNotNil(savedDoc2.issues["issue-readable-2"])

        let captured = await fakeSource.capturedTokens
        XCTAssertEqual(captured.count, 2)
        XCTAssertEqual(captured[0], Data("token-initial".utf8))
        XCTAssertEqual(captured[1], Data("token-initial".utf8))
    }

    // MARK: - 10. Page With Malformed Issue Fields Keeps Previous Token and Throws

    func testPageWithMalformedIssueRecordStoresReadableIssuesKeepsPreviousTokenAndThrows() async throws {
        let fileURL = makeTemporaryFileURL()
        defer { try? FileManager.default.removeItem(at: fileURL.deletingLastPathComponent()) }

        let initialIssue = try makeTestIssue(id: "issue-initial")
        let initialDoc = IssueInboxDocument(
            changeToken: Data("token-initial".utf8),
            issues: ["issue-initial": IssueInboxEntry(
                issue: initialIssue,
                reportedAt: Date(timeIntervalSince1970: 100),
                receivedAt: Date(timeIntervalSince1970: 100)
            )]
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .prettyPrinted]
        try FileManager.default.createDirectory(at: fileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try encoder.encode(initialDoc).write(to: fileURL)

        let fakeSource = FakeIssueInboxSource()

        let readableIssue = try makeTestIssue(id: "issue-readable")
        let readableRecord = IssueInboxRecord(
            record: try CloudKitMapping.issueRecord(readableIssue),
            serverCreationDate: Date(timeIntervalSince1970: 1000)
        )

        // Malformed issue: kind is .issue but schema version is 99 so CloudKitMapping.issue(from:) throws
        let malformedMappedRecord = try CloudKitMappedRecord(
            kind: .issue,
            recordName: "QuestionIssue/issue-malformed",
            fields: ["schema_version": .integer(99)]
        )
        let malformedRecord = IssueInboxRecord(record: malformedMappedRecord)

        await fakeSource.enqueuePage(IssueInboxPage(
            records: [readableRecord, malformedRecord],
            unreadableIssueRecordNames: [],
            changeToken: Data("token-bad-page".utf8),
            moreComing: false
        ))

        nonisolated(unsafe) var emittedStatuses: [IssueInboxStatus] = []
        let reader = IssueInboxReader(
            source: fakeSource,
            fileURL: fileURL,
            onStatus: { emittedStatuses.append($0) }
        )

        do {
            _ = try await reader.refresh()
            XCTFail("Expected refresh to throw unreadableIssueRecord")
        } catch {
            XCTAssertEqual(error as? IssueInboxSourceError, .unreadableIssueRecord)
        }

        XCTAssertTrue(emittedStatuses.contains(.failed(.unreadableIssueRecord)))

        let savedData = try Data(contentsOf: fileURL)
        let savedDoc = try JSONDecoder().decode(IssueInboxDocument.self, from: savedData)
        XCTAssertEqual(savedDoc.changeToken, Data("token-initial".utf8))
        XCTAssertNotNil(savedDoc.issues["issue-readable"])
        XCTAssertNotNil(savedDoc.issues["issue-initial"])
    }

    // MARK: - 11. Token Decode Helper

#if canImport(CloudKit)
    func testTokenDecodeHelperReturnsNilForNilAndThrowsExpiredForInvalidData() throws {
        guard #available(iOS 17.0, macOS 14.0, *) else { return }
        XCTAssertNil(try CloudKitIssueInboxSource.decodeServerChangeToken(from: nil))
        XCTAssertThrowsError(try CloudKitIssueInboxSource.decodeServerChangeToken(from: Data("not-a-token".utf8))) { error in
            XCTAssertEqual(error as? IssueInboxSourceError, .changeTokenExpired)
        }
    }
#endif
}
