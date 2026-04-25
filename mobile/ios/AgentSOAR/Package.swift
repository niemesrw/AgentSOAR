// swift-tools-version:5.9
// AgentSOARKit — Swift SDK for the AgentSOAR Bedrock AgentCore runtime.
//
// Builds:
//   • AgentSOARKit — embeddable library (auth, streaming, AG-UI parsing)
//   • AgentSOARKitTests — unit tests for the parser and SSE reader
//
// Apple-only: depends on CryptoKit and Security. `swift test` runs on macOS;
// the full app requires Xcode + iOS 16.

import PackageDescription

let package = Package(
    name: "AgentSOARKit",
    platforms: [
        .iOS(.v16),
        .macOS(.v13),
    ],
    products: [
        .library(
            name: "AgentSOARKit",
            targets: ["AgentSOARKit"]
        ),
    ],
    targets: [
        .target(
            name: "AgentSOARKit",
            path: "Sources/AgentSOARKit"
        ),
        .testTarget(
            name: "AgentSOARKitTests",
            dependencies: ["AgentSOARKit"],
            path: "Tests/AgentSOARKitTests"
        ),
    ]
)
