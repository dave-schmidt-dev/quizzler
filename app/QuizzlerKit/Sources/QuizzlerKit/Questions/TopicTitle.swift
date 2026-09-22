import Foundation

/// Turns a pack's machine topic into something a learner can read.
///
/// Pack topics are authored as slugs (`d1-separation-of-duties-two-person-control`)
/// because they are identity: selection, weakness tracking and issue reports all
/// key on the exact string. The walkthrough found them rendered raw on every
/// question screen, so the display form is derived here rather than by editing
/// 203 rows of content — the slug stays the identity, this is only the label.
public enum TopicTitle {
    /// Words that read wrong in title case and are always upper-cased.
    ///
    /// Drawn from the installed CISSP and CySA+ packs. A word not listed here
    /// is capitalized normally, so an unknown pack degrades to plain title case
    /// rather than to a wrong expansion.
    private static let acronyms: Set<String> = [
        "aaa", "api", "arp", "bc", "bcp", "casb", "cdn", "cia", "cm", "coop", "cpted",
        "cve", "dlp", "dns", "drm", "drp", "edrm", "eol", "eos", "hids", "hipaa",
        "iaaa", "ics", "ids", "iot", "ip", "ips", "kpi", "kri", "mac", "nac", "nat",
        "nids", "nist", "osi", "ot", "phi", "pii", "pki", "raid", "scada", "scap",
        "scrm", "sdlc", "sdn", "siem", "soc", "sso", "voip", "vpn", "xss"
    ]

    /// Slug words whose readable form is not just a case change.
    private static let expansions: [String: String] = [
        "cicd": "CI/CD",
        "tcpip": "TCP/IP",
        "isc2": "ISC2",
        "ediscovery": "eDiscovery"
    ]

    /// Words kept lower-case unless they open the title.
    private static let minorWords: Set<String> = ["and", "of", "to", "on", "for", "in", "the", "a", "an", "vs"]

    /// The readable form of a topic slug. A slug that is already prose is
    /// returned unchanged, so a pack that authors real titles is not mangled.
    public static func display(_ topic: String) -> String {
        let trimmed = topic.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return trimmed }
        guard !trimmed.contains(" ") else { return trimmed }

        // A leading `d3-` is the exam domain, which the learner explicitly does
        // not want surfaced — the engine keeps it, the label drops it.
        let withoutDomain = trimmed.replacingOccurrences(
            of: #"^d\d+-"#,
            with: "",
            options: [.regularExpression, .caseInsensitive]
        )

        let words = withoutDomain.split(separator: "-").map(String.init)
        guard !words.isEmpty else { return trimmed }

        return words.enumerated()
            .map { index, word in format(word, isFirst: index == 0) }
            .joined(separator: " ")
    }

    private static func format(_ raw: String, isFirst: Bool) -> String {
        let lower = raw.lowercased()
        // Returned verbatim in any position: `eDiscovery` is wrong capitalized.
        if let expansion = expansions[lower] { return expansion }
        if acronyms.contains(lower) { return lower.uppercased() }
        if !isFirst, minorWords.contains(lower) { return lower }
        // A word that starts with a digit (`53a`, `800`) has no case to change.
        guard let first = lower.first, first.isLetter else { return lower.uppercased() }
        return lower.prefix(1).uppercased() + lower.dropFirst()
    }
}
