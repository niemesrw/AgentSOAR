import Foundation

/// Parser-emitted events. Mirrors the `StreamEvent` union in
/// `frontend/src/lib/agentcore-client/types.ts` so any pattern parser
/// can be adapted to the same downstream UI.
public enum StreamEvent: Sendable, Equatable {
    case text(String)
    case toolUseStart(toolUseId: String, name: String)
    case toolUseDelta(toolUseId: String, input: String)
    case toolResult(toolUseId: String, result: String)
    case result(stopReason: String)
    case lifecycle(String)
}

/// Parses one SSE line into zero or more `StreamEvent`s.
public protocol ChunkParser: Sendable {
    func parse(line: String, emit: (StreamEvent) -> Void)
}
