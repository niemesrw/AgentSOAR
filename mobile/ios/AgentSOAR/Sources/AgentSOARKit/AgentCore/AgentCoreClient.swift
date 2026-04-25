import Foundation

public enum AgentCoreError: Error, LocalizedError {
    case missingAccessToken
    case missingRuntimeArn
    case http(status: Int, body: String)
    case invalidResponse

    public var errorDescription: String? {
        switch self {
        case .missingAccessToken: return "No valid access token found."
        case .missingRuntimeArn: return "Agent Runtime ARN not configured."
        case let .http(status, body): return "HTTP \(status): \(body)"
        case .invalidResponse: return "Invalid response from AgentCore Runtime."
        }
    }
}

/// Thin Swift port of `AgentCoreClient` from
/// `frontend/src/lib/agentcore-client/client.ts`.
///
/// Builds the AG-UI request payload, opens a streamed POST to
/// `bedrock-agentcore.{region}.amazonaws.com`, and yields parsed events.
public final class AgentCoreClient: Sendable {
    private let runtimeArn: String
    private let region: String
    private let pattern: String
    private let parser: ChunkParser
    private let session: URLSession

    public init(config: AgentCoreConfig, session: URLSession = .shared) {
        self.runtimeArn = config.agentRuntimeArn
        self.region = config.awsRegion
        self.pattern = config.agentPattern
        self.parser = ParserResolver.parser(for: config.agentPattern)
        self.session = session
    }

    public func generateSessionId() -> String {
        UUID().uuidString.lowercased()
    }

    /// Streams events for a single user turn.
    ///
    /// User identity is extracted server-side from the validated JWT token,
    /// not sent in the payload body — this prevents impersonation via prompt
    /// injection (see `client.ts` for the same comment).
    public func invoke(
        query: String,
        sessionId: String,
        accessToken: String
    ) async throws -> AsyncThrowingStream<StreamEvent, Error> {
        guard !accessToken.isEmpty else { throw AgentCoreError.missingAccessToken }
        guard !runtimeArn.isEmpty else { throw AgentCoreError.missingRuntimeArn }

        let endpoint = "https://bedrock-agentcore.\(region).amazonaws.com"
        let escapedArn = runtimeArn.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed.subtracting(CharacterSet(charactersIn: "/:"))) ?? runtimeArn
        guard let url = URL(string: "\(endpoint)/runtimes/\(escapedArn)/invocations?qualifier=DEFAULT") else {
            throw AgentCoreError.invalidResponse
        }

        let traceId = "1-\(String(Int(Date().timeIntervalSince1970), radix: 16))-\(UUID().uuidString.lowercased())"

        let body: [String: Any] = pattern.hasPrefix("agui-")
            ? [
                "threadId": sessionId,
                "runId": UUID().uuidString.lowercased(),
                "messages": [[
                    "id": UUID().uuidString.lowercased(),
                    "role": "user",
                    "content": query,
                ]],
                "state": [String: Any](),
                "tools": [Any](),
                "context": [Any](),
                "forwardedProps": [String: Any](),
            ]
            : [
                "prompt": query,
                "runtimeSessionId": sessionId,
            ]

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
        request.setValue(traceId, forHTTPHeaderField: "X-Amzn-Trace-Id")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(sessionId, forHTTPHeaderField: "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (bytes, response) = try await session.bytes(for: request)
        guard let http = response as? HTTPURLResponse else { throw AgentCoreError.invalidResponse }

        guard (200..<300).contains(http.statusCode) else {
            var bodyText = ""
            for try await byte in bytes {
                bodyText.append(Character(UnicodeScalar(byte)))
            }
            throw AgentCoreError.http(status: http.statusCode, body: bodyText)
        }

        return SSEStream.read(bytes: bytes, parser: parser)
    }
}
