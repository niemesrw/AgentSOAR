// swift-tools-version:5.9
// AgentSOARKit — Swift SDK for the AgentSOAR Bedrock AgentCore runtime.
//
// Builds:
//   • AgentSOARKit — embeddable library (auth, streaming, AG-UI parsing)
//   • AgentSOARKitTests — unit tests for the parser and SSE reader

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
