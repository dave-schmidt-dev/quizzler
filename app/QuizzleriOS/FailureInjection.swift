#if DEBUG
import Foundation

/// Test-only launch controls for fail-visible Development probe checks.
///
/// Release excludes this file through `EXCLUDED_SOURCE_FILE_NAMES`; the
/// production/default app path therefore has no failure-injection setting.
enum DevelopmentProbeFailureInjection {
    static let environmentKey = "QUIZZLER_DEVELOPMENT_CLOUDKIT_PROBE_INJECT_FAILURE"
    static let unavailableEntitlementOrAccount = "unavailable_entitlement_or_account"

    static var isUnavailableEntitlementOrAccountEnabled: Bool {
        ProcessInfo.processInfo.environment[environmentKey] == unavailableEntitlementOrAccount
    }
}
#endif
