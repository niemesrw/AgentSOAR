import Foundation

/// Configuration for the AgentSOAR runtime, loaded from `aws-exports.json`.
///
/// Mirrors the shape consumed by the React frontend (`frontend/src/lib/auth.ts`)
/// so the same `aws-exports.json` can drive both clients.
public struct AgentCoreConfig: Codable, Sendable, Equatable {
    public let agentRuntimeArn: String
    public let awsRegion: String
    public let agentPattern: String

    public let authority: String
    public let clientId: String
    public let redirectUri: String
    public let scope: String
    public let responseType: String

    public init(
        agentRuntimeArn: String,
        awsRegion: String,
        agentPattern: String,
        authority: String,
        clientId: String,
        redirectUri: String,
        scope: String = "email openid profile",
        responseType: String = "code"
    ) {
        self.agentRuntimeArn = agentRuntimeArn
        self.awsRegion = awsRegion
        self.agentPattern = agentPattern
        self.authority = authority
        self.clientId = clientId
        self.redirectUri = redirectUri
        self.scope = scope
        self.responseType = responseType
    }

    private enum CodingKeys: String, CodingKey {
        case agentRuntimeArn
        case awsRegion
        case agentPattern
        case authority
        case clientId = "client_id"
        case redirectUri = "redirect_uri"
        case scope
        case responseType = "response_type"
    }

    /// Decode from a bundled `aws-exports.json` resource.
    public static func load(from url: URL) throws -> AgentCoreConfig {
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(AgentCoreConfig.self, from: data)
    }
}
