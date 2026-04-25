import Foundation

/// One bubble in the chat transcript. Tool calls are tracked alongside the
/// assistant message that triggered them so they render inline.
public struct ChatMessage: Identifiable, Equatable {
    public enum Role: String { case user, assistant, system }

    public struct ToolCall: Identifiable, Equatable {
        public let id: String
        public var name: String
        public var args: String
        public var result: String?
        public init(id: String, name: String, args: String = "", result: String? = nil) {
            self.id = id; self.name = name; self.args = args; self.result = result
        }
    }

    public let id: UUID
    public let role: Role
    public var text: String
    public var toolCalls: [ToolCall]
    public var isStreaming: Bool

    public init(
        id: UUID = UUID(),
        role: Role,
        text: String = "",
        toolCalls: [ToolCall] = [],
        isStreaming: Bool = false
    ) {
        self.id = id
        self.role = role
        self.text = text
        self.toolCalls = toolCalls
        self.isStreaming = isStreaming
    }
}
