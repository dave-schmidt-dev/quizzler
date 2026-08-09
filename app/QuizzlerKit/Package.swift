// swift-tools-version: 6.0
import PackageDescription

// Contract tests stay dependency-free and offline. Keep production and test
// source roots disjoint so SwiftPM cannot compile a test file into the module.
let package = Package(
    name: "QuizzlerKit",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [.library(name: "QuizzlerKit", targets: ["QuizzlerKit"])],
    targets: [
        .target(name: "QuizzlerKit", path: "Sources/QuizzlerKit"),
        .testTarget(name: "QuizzlerKitTests", dependencies: ["QuizzlerKit"], path: "Tests/QuizzlerKitTests")
    ]
)
