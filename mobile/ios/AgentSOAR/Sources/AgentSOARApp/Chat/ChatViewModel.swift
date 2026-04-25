import Foundation
import AgentSOARKit

@MainActor
public final class ChatViewModel: ObservableObject {
    @Published public private(set) var messages: [ChatMessage] = []
    @Published public private(set) var isLoading: Bool = false
    @Published public var draft: String = ""
    @Published public var error: String?

    private let client: AgentCoreClient
    private let auth: CognitoAuthClient
    private var sessionId: String

    public init(client: AgentCoreClient, auth: CognitoAuthClient) {
        self.client = client
        self.auth = auth
        self.sessionId = client.generateSessionId()
    }

    public func resetSession() {
        sessionId = client.generateSessionId()
        messages.removeAll()
    }

    public func send() async {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isLoading else { return }
        draft = ""
        error = nil

        messages.append(ChatMessage(role: .user, text: text))
        var assistant = ChatMessage(role: .assistant, isStreaming: true)
        messages.append(assistant)
        let assistantId = assistant.id
        isLoading = true
        defer { isLoading = false }

        do {
            guard let token = try await auth.validAccessToken() else {
                error = "Sign in required."
                markFinished(id: assistantId)
                return
            }

            let stream = try await client.invoke(
                query: text,
                sessionId: sessionId,
                accessToken: token
            )

            for try await event in stream {
                apply(event: event, to: assistantId, snapshot: &assistant)
            }
        } catch {
            self.error = error.localizedDescription
        }
        markFinished(id: assistantId)
    }

    private func apply(event: StreamEvent, to id: UUID, snapshot: inout ChatMessage) {
        guard let idx = messages.firstIndex(where: { $0.id == id }) else { return }
        switch event {
        case let .text(content):
            messages[idx].text.append(content)
        case let .toolUseStart(toolUseId, name):
            messages[idx].toolCalls.append(.init(id: toolUseId, name: name))
        case let .toolUseDelta(toolUseId, input):
            if let i = messages[idx].toolCalls.firstIndex(where: { $0.id == toolUseId }) {
                messages[idx].toolCalls[i].args.append(input)
            }
        case let .toolResult(toolUseId, result):
            if let i = messages[idx].toolCalls.firstIndex(where: { $0.id == toolUseId }) {
                messages[idx].toolCalls[i].result = result
            }
        case .result, .lifecycle:
            break
        }
    }

    private func markFinished(id: UUID) {
        if let idx = messages.firstIndex(where: { $0.id == id }) {
            messages[idx].isStreaming = false
        }
    }
}
