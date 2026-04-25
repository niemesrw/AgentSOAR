import Foundation

/// Reads a Server-Sent-Events stream from `URLSession.bytes(for:)`, splitting
/// on `\n` and feeding each non-empty line through a `ChunkParser`.
///
/// AgentCore Runtime streams AG-UI events as `data: <JSON>\n\n`. We treat
/// every newline-delimited fragment as a candidate line for the parser, which
/// handles the `data: ` prefix itself.
public enum SSEStream {
    public static func read(
        bytes: URLSession.AsyncBytes,
        parser: ChunkParser
    ) -> AsyncThrowingStream<StreamEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    var buffer = ""
                    for try await byte in bytes {
                        let scalar = UnicodeScalar(byte)
                        let char = Character(scalar)
                        if char == "\n" {
                            let line = buffer
                            buffer = ""
                            guard !line.trimmingCharacters(in: .whitespaces).isEmpty else { continue }
                            parser.parse(line: line) { event in
                                continuation.yield(event)
                            }
                        } else {
                            buffer.append(char)
                        }
                    }
                    if !buffer.trimmingCharacters(in: .whitespaces).isEmpty {
                        parser.parse(line: buffer) { event in
                            continuation.yield(event)
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    /// Variant for tests / non-URLSession sources: parse a raw String body.
    public static func read(
        body: String,
        parser: ChunkParser
    ) -> [StreamEvent] {
        var out: [StreamEvent] = []
        for line in body.split(separator: "\n", omittingEmptySubsequences: false) {
            let trimmed = String(line).trimmingCharacters(in: .whitespaces)
            guard !trimmed.isEmpty else { continue }
            parser.parse(line: String(line)) { event in out.append(event) }
        }
        return out
    }
}
