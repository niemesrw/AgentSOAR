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
    private var sendTask: Task<Void, Never>?

    public init(client: AgentCoreClient, auth: CognitoAuthClient) {
        self.client = client
        self.auth = auth
        self.sessionId = client.generateSessionId()
    }

    public func resetSession() {
        sendTask?.cancel()
        sendTask = nil
        sessionId = client.generateSessionId()
        messages.removeAll()
    }

    /// Cancels the in-flight stream, if any. Safe to call when idle.
    public func cancel() {
        sendTask?.cancel()
    }

    public func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isLoading else { return }
        draft = ""
        error = nil

        messages.append(ChatMessage(role: .user, text: text))
        let assistant = ChatMessage(role: .assistant, isStreaming: true)
        messages.append(assistant)
        let assistantId = assistant.id
        isLoading = true

        sendTask = Task { @MainActor [weak self] in
            guard let self else { return }
            defer {
                self.markFinished(id: assistantId)
                self.isLoading = false
                self.sendTask = nil
            }
            do {
                guard let token = try await self.auth.validAccessToken() else {
                    self.error = "Sign in required."
                    return
                }

                let stream = try await self.client.invoke(
                    query: text,
                    sessionId: self.sessionId,
                    accessToken: token
                )

                for try await event in stream {
                    if Task.isCancelled { break }
                    self.apply(event: event, to: assistantId)
                }
            } catch is CancellationError {
                // user cancelled — keep partial message, clear streaming state
            } catch {
                self.error = error.localizedDescription
            }
        }
    }

    private func apply(event: StreamEvent, to id: UUID) {
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
