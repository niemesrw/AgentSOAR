import XCTest
@testable import AgentSOARKit

final class AGUIParserTests: XCTestCase {
    private func parse(_ body: String) -> [StreamEvent] {
        SSEStream.read(body: body, parser: AGUIParser())
    }

    func testTextMessageContentEmitsTextEvent() {
        let body = #"data: {"type":"TEXT_MESSAGE_CONTENT","delta":"Hello"}"#
        XCTAssertEqual(parse(body), [.text("Hello")])
    }

    func testTextMessageContentMissingDeltaEmitsEmptyText() {
        let body = #"data: {"type":"TEXT_MESSAGE_CONTENT"}"#
        XCTAssertEqual(parse(body), [.text("")])
    }

    func testToolCallStartEmitsToolUseStart() {
        let body = #"data: {"type":"TOOL_CALL_START","toolCallId":"tool_1","toolCallName":"lookup_finding"}"#
        XCTAssertEqual(parse(body), [.toolUseStart(toolUseId: "tool_1", name: "lookup_finding")])
    }

    func testToolCallArgsEmitsToolUseDelta() {
        let body = #"data: {"type":"TOOL_CALL_ARGS","toolCallId":"tool_1","delta":"{\"id\":\"abc\"}"}"#
        XCTAssertEqual(parse(body), [.toolUseDelta(toolUseId: "tool_1", input: #"{"id":"abc"}"#)])
    }

    func testToolCallResultEmitsToolResult() {
        let body = #"data: {"type":"TOOL_CALL_RESULT","toolCallId":"tool_1","content":"OK"}"#
        XCTAssertEqual(parse(body), [.toolResult(toolUseId: "tool_1", result: "OK")])
    }

    func testRunFinishedEmitsResultAndLifecycle() {
        let body = #"data: {"type":"RUN_FINISHED"}"#
        XCTAssertEqual(parse(body), [
            .result(stopReason: "end_turn"),
            .lifecycle("run_finished"),
        ])
    }

    func testRunStartedEmitsLifecycleOnly() {
        let body = #"data: {"type":"RUN_STARTED"}"#
        XCTAssertEqual(parse(body), [.lifecycle("run_started")])
    }

    func testStateSnapshotIsIgnored() {
        let body = #"data: {"type":"STATE_SNAPSHOT","state":{}}"#
        XCTAssertTrue(parse(body).isEmpty)
    }

    func testNonDataLineIsIgnored() {
        let body = #"event: ping"#
        XCTAssertTrue(parse(body).isEmpty)
    }

    func testMalformedJSONIsIgnoredSilently() {
        let body = #"data: {not json"#
        XCTAssertTrue(parse(body).isEmpty)
    }

    func testFullStreamEndToEnd() {
        let body = """
        data: {"type":"RUN_STARTED"}
        data: {"type":"TEXT_MESSAGE_START"}
        data: {"type":"TEXT_MESSAGE_CONTENT","delta":"Hi "}
        data: {"type":"TEXT_MESSAGE_CONTENT","delta":"there"}
        data: {"type":"TOOL_CALL_START","toolCallId":"t1","toolCallName":"lookup"}
        data: {"type":"TOOL_CALL_ARGS","toolCallId":"t1","delta":"{}"}
        data: {"type":"TOOL_CALL_RESULT","toolCallId":"t1","content":"done"}
        data: {"type":"TEXT_MESSAGE_END"}
        data: {"type":"RUN_FINISHED"}
        """
        XCTAssertEqual(parse(body), [
            .lifecycle("run_started"),
            .lifecycle("message_start"),
            .text("Hi "),
            .text("there"),
            .toolUseStart(toolUseId: "t1", name: "lookup"),
            .toolUseDelta(toolUseId: "t1", input: "{}"),
            .toolResult(toolUseId: "t1", result: "done"),
            .lifecycle("message_end"),
            .result(stopReason: "end_turn"),
            .lifecycle("run_finished"),
        ])
    }
}
