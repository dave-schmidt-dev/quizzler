/// The package boundary is intentionally dependency-free until native domain
/// types land. Keeping a real production source root makes SwiftPM and the
/// XcodeGen framework target resolve the same module without test overlap.
public enum QuizzlerKit {
    public static let contractVersion = 1
}
