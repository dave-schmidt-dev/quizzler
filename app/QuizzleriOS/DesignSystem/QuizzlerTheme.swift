import SwiftUI

/// Shared visual tokens for the Zero Delta Study Console.
public enum QuizzlerTheme {
    /// The terminal-like canvas behind the console.
    public static let terminalBackground = Color(red: 0x14 / 255.0, green: 0x14 / 255.0, blue: 0x19 / 255.0)

    /// The elevated surface used for study cards.
    public static let elevatedCard = Color(red: 0x20 / 255.0, green: 0x2A / 255.0, blue: 0x38 / 255.0)

    /// The primary cyan action and question cue color.
    public static let primaryCyan = Color(red: 0x00, green: 0xFF / 255.0, blue: 0xFF / 255.0)

    /// The cyan fill for a question-cue card.
    public static let questionCueCard = primaryCyan

    /// The high-contrast text color for primary content.
    public static let textPrimary = Color(red: 0xF5 / 255.0, green: 0xF5 / 255.0, blue: 0xF5 / 255.0)

    /// The subdued text color for supporting content.
    public static let textMuted = Color(red: 0x9A / 255.0, green: 0x9A / 255.0, blue: 0xA2 / 255.0)

    /// The answer-notch and positive-feedback color.
    public static let success = Color(red: 0x87 / 255.0, green: 0xD7 / 255.0, blue: 0x87 / 255.0)

    /// The green marker used as an answer notch.
    public static let answerNotch = success

    /// The color for cautionary states.
    public static let warning = Color(red: 0xFF / 255.0, green: 0xD7 / 255.0, blue: 0x5F / 255.0)

    /// The color for error and destructive states.
    public static let danger = Color(red: 0xFF / 255.0, green: 0x5F / 255.0, blue: 0x5F / 255.0)

    /// The horizontal page inset.
    public static let pageGutter: CGFloat = 24

    /// The standard vertical and horizontal stack spacing.
    public static let stackGap: CGFloat = 8

    /// The corner radius for elevated cards.
    public static let cardRadius: CGFloat = 8

    /// The minimum interactive target dimension.
    public static let minimumTouchTarget: CGFloat = 44

    /// The readable system sans used for study content.
    public static let readableFont = Font.system(.body, design: .default)

    /// The monospaced font used for metadata and technical labels.
    public static let metadataFont = Font.system(.caption, design: .monospaced)
}
