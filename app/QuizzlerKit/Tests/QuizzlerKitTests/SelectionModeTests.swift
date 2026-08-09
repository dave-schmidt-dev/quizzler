import XCTest
@testable import QuizzlerKit

final class SelectionModeTests: XCTestCase {
    func testModesHaveExplicitWireValues() {
        XCTAssertEqual(SelectionMode.normal.rawValue, "normal")
        XCTAssertEqual(SelectionMode.retryMissed.rawValue, "retry_missed")
        XCTAssertEqual(SelectionMode.srs.rawValue, "srs")
    }

    func testSRSStateUsesBoundedTier() throws {
        XCTAssertNoThrow(try SRSState(tier: 7, nextDueAt: Date()))
        XCTAssertThrowsError(try SRSState(tier: 8, nextDueAt: Date()))
    }
}
