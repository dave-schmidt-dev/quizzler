// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "ZeroDeltaSting",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "ZeroDeltaSting", targets: ["ZeroDeltaSting"]),
    ],
    targets: [
        .target(name: "ZeroDeltaSting"),
    ]
)
