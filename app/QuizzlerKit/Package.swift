// swift-tools-version: 6.0
import PackageDescription

// Local pilot dependency resolves offline. Keep production and test
// source roots disjoint so SwiftPM cannot compile a test file into the module.
let package = Package(
    name: "QuizzlerKit",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [.library(name: "QuizzlerKit", targets: ["QuizzlerKit"])],
    dependencies: [.package(path: "../../../apple_developer/diagnostics_contracts/swift")],
    targets: [
        .target(name: "QuizzlerKit", dependencies: [.product(name: "AppDiagnostics", package: "swift")], path: "Sources/QuizzlerKit"),
        .testTarget(name: "QuizzlerKitTests", dependencies: ["QuizzlerKit"], path: "Tests/QuizzlerKitTests")
    ]
)
