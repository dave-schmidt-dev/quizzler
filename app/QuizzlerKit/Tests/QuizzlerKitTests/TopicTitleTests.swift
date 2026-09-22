import XCTest
@testable import QuizzlerKit

/// The defect this covers: every question screen printed the pack's raw topic
/// slug (`d1-separation-of-duties-two-person-control`) as the learner-facing
/// label.
final class TopicTitleTests: XCTestCase {
    func testDomainPrefixIsDroppedAndWordsAreReadable() {
        XCTAssertEqual(
            TopicTitle.display("d1-separation-of-duties-two-person-control"),
            "Separation of Duties Two Person Control"
        )
        XCTAssertEqual(TopicTitle.display("d3-emanation-security"), "Emanation Security")
        XCTAssertEqual(TopicTitle.display("d7-alternate-site-strategies"), "Alternate Site Strategies")
    }

    func testAcronymsAreNotTitleCased() {
        XCTAssertEqual(TopicTitle.display("d1-cia-triad"), "CIA Triad")
        XCTAssertEqual(TopicTitle.display("d2-casb"), "CASB")
        XCTAssertEqual(TopicTitle.display("d3-digital-signatures-pki"), "Digital Signatures PKI")
        XCTAssertEqual(TopicTitle.display("d4-siem-correlation"), "SIEM Correlation")
    }

    func testMinorWordsStayLowerUnlessTheyOpenTheTitle() {
        XCTAssertEqual(
            TopicTitle.display("d5-provisioning-and-deprovisioning-lifecycle"),
            "Provisioning and Deprovisioning Lifecycle"
        )
        XCTAssertEqual(TopicTitle.display("of-counsel-review"), "Of Counsel Review")
    }

    func testWordsWithNoPlainCaseFormAreExpanded() {
        XCTAssertEqual(TopicTitle.display("d8-cicd-pipeline-controls"), "CI/CD Pipeline Controls")
        XCTAssertEqual(TopicTitle.display("d4-tcpip-model"), "TCP/IP Model")
        XCTAssertEqual(TopicTitle.display("d1-ediscovery-edrm-model"), "eDiscovery EDRM Model")
        XCTAssertEqual(TopicTitle.display("d1-isc2-code-of-ethics"), "ISC2 Code of Ethics")
    }

    func testDigitLedWordsSurviveUnchanged() {
        XCTAssertEqual(TopicTitle.display("d6-nist-800-53a-assessment"), "NIST 800 53A Assessment")
    }

    /// A pack that already authors readable topics must not be mangled, and an
    /// empty topic must not become a stray label.
    func testProseTopicsAndEmptyInputAreReturnedUnchanged() {
        XCTAssertEqual(TopicTitle.display("Threats and mitigations"), "Threats and mitigations")
        XCTAssertEqual(TopicTitle.display(""), "")
        XCTAssertEqual(TopicTitle.display("   "), "")
        XCTAssertEqual(TopicTitle.display("astronomy"), "Astronomy")
    }
}
