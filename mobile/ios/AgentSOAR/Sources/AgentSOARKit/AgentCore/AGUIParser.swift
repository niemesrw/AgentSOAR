import Foundation

/// Parses AG-UI SSE events from `agui-strands-agent` and friends.
///
/// AG-UI events arrive as `data: <JSON>` where each JSON object has a `type` field.
/// Direct port of `frontend/src/lib/agentcore-client/parsers/agui.ts`.
public struct AGUIParser: ChunkParser {
    public init() {}

    public func parse(line: String, emit: (StreamEvent) -> Void) {
        guard line.hasPrefix("data: ") else { return }

        let payload = line.dropFirst(6).trimmingCharacters(in: .whitespaces)
        guard !payload.isEmpty,
              let data = payload.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let eventType = json["type"] as? String
        else { return }

        switch eventType {
        case "TEXT_MESSAGE_CONTENT":
            emit(.text((json["delta"] as? String) ?? ""))

        case "TOOL_CALL_START":
            emit(.toolUseStart(
                toolUseId: (json["toolCallId"] as? String) ?? "",
                name: (json["toolCallName"] as? String) ?? ""
            ))

        case "TOOL_CALL_ARGS":
            emit(.toolUseDelta(
                toolUseId: (json["toolCallId"] as? String) ?? "",
                input: (json["delta"] as? String) ?? ""
            ))

        case "TOOL_CALL_RESULT":
            emit(.toolResult(
                toolUseId: (json["toolCallId"] as? String) ?? "",
                result: (json["content"] as? String) ?? ""
            ))

        case "RUN_FINISHED":
            emit(.result(stopReason: "end_turn"))
            emit(.lifecycle("run_finished"))

        case "RUN_STARTED":
            emit(.lifecycle("run_started"))

        case "TEXT_MESSAGE_START":
            emit(.lifecycle("message_start"))

        case "TEXT_MESSAGE_END":
            emit(.lifecycle("message_end"))

        case "STATE_SNAPSHOT", "TOOL_CALL_END":
            // Informational — no-op
            break

        default:
            break
        }
    }
}

/// Resolves a parser from a pattern prefix. Currently only AG-UI is wired up
/// for the mobile client (matching the active deployment); add other parsers
/// as needed.
public enum ParserResolver {
    public static func parser(for pattern: String) -> ChunkParser {
        if pattern.hasPrefix("agui-") {
            return AGUIParser()
        }
        // Default to AG-UI; extend with strands/langgraph/claude parsers as the
        // backend exposes them on mobile.
        return AGUIParser()
    }
}
