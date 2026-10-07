import XCTest

/// C20: attaches a screenshot of the current screen to `test`'s result bundle
/// with a stable, ordered name ("01-today" …), so review captures can be
/// exported one screen at a time without keeping every step of every test.
@MainActor
func captureReviewScreen(_ app: XCUIApplication, _ name: String, in test: XCTestCase) {
    let attachment = XCTAttachment(screenshot: app.screenshot())
    attachment.name = name
    attachment.lifetime = .keepAlways
    test.add(attachment)
}
