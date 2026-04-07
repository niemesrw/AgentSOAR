"""
A2A request/response flow tests for the investigation agent.

These tests send a real A2A JSON-RPC request to POST / and verify the executor
is wired correctly and returns valid A2A events — without any AWS credentials.

Tests:
  - test_a2a_message_send_reaches_executor: POST / with message/send → executor called
  - test_a2a_message_send_returns_task_response: response is a valid A2A Task JSON
  - test_executor_creates_agent_per_request: each invocation creates a fresh agent
  - test_investigate_tool_construction: _make_investigation_tool builds correct headers/card

Run:
    uv run pytest tests/unit/test_investigation_agent_a2a.py -v
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent
_SYS_PATH_ADDITIONS = [
    str(_REPO_ROOT / "patterns"),
    str(_REPO_ROOT / "patterns" / "agui-strands-agent"),
    str(_REPO_ROOT / "patterns" / "investigation-agent"),
]
_MODULES_TO_EVICT = frozenset(
    ["main", "agent", "tools", "tools.gateway", "utils", "utils.ssm", "utils.auth"]
)

_A2A_MESSAGE_SEND = {
    "id": "test-1",
    "jsonrpc": "2.0",
    "method": "message/send",
    "params": {
        "message": {
            "kind": "message",
            "messageId": "msg-test-1",
            "role": "user",
            "parts": [
                {"kind": "text", "text": "investigate finding foo in account 123"}
            ],
        }
    },
}

# Fake workload token header — sets BedrockAgentCoreContext so
# @requires_access_token doesn't raise "Workload access token has not been set"
_FAKE_HEADERS = {
    "X-Amzn-Bedrock-AgentCore-Runtime-Access-Token": "fake-workload-token",
    "Content-Type": "application/json",
}


@pytest.fixture(autouse=True)
def _agent_env(monkeypatch):
    monkeypatch.setenv("STACK_NAME", "AgentSOAR-test")
    monkeypatch.setenv("GATEWAY_CREDENTIAL_PROVIDER_NAME", "test-provider")
    monkeypatch.setenv("AGENTCORE_RUNTIME_URL", "http://localhost:8080")
    monkeypatch.setenv("MEMORY_ID", "test-memory-id")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture(autouse=True)
def _agent_sys_path():
    added = []
    for d in _SYS_PATH_ADDITIONS:
        if d not in sys.path:
            sys.path.insert(0, d)
            added.append(d)
    yield
    for d in added:
        sys.path.remove(d)
    for mod in [
        m for m in sys.modules if m in _MODULES_TO_EVICT or m.startswith("tools.")
    ]:
        del sys.modules[mod]


def _make_mock_agent(response_text="Investigation complete."):
    """Build a mock Strands agent whose stream_async yields a single result event."""
    from strands.agent.agent_result import AgentResult

    # AgentResult needs a real message structure; use a minimal mock
    mock_result = MagicMock(spec=AgentResult)
    mock_result.__str__ = lambda self: response_text

    async def _stream_async(content_blocks, invocation_state=None):
        yield {"result": mock_result}

    mock_agent = MagicMock()
    mock_agent.stream_async = _stream_async
    mock_agent.name = "investigation_agent"
    mock_agent.description = "test agent"
    return mock_agent


@pytest.fixture()
def app_with_mock_agent():
    """Return the Starlette app with create_agent mocked to return a controllable agent."""
    mock_agent = _make_mock_agent()
    with (
        patch("utils.ssm.get_ssm_parameter", return_value="https://fake-url"),
        patch("agent.create_agent", return_value=mock_agent) as mock_create,
    ):
        import main

        yield main.app, mock_create, mock_agent


def test_a2a_message_send_reaches_executor(app_with_mock_agent):
    """POST / with a valid A2A message/send body must reach the executor.

    If the executor is not called, the wiring between build_a2a_app and
    PerRequestExecutor is broken.
    """
    from starlette.testclient import TestClient

    app, mock_create, _ = app_with_mock_agent
    client = TestClient(app, raise_server_exceptions=True)

    resp = client.post("/", json=_A2A_MESSAGE_SEND, headers=_FAKE_HEADERS)

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    # create_agent must have been called exactly once — once per request
    mock_create.assert_called_once()


def test_a2a_message_send_returns_task_response(app_with_mock_agent):
    """POST / must return a valid A2A Task JSON with state=completed.

    Catches mis-wired executors that return garbage or no response body.
    """
    from starlette.testclient import TestClient

    app, _, _ = app_with_mock_agent
    client = TestClient(app, raise_server_exceptions=True)

    resp = client.post("/", json=_A2A_MESSAGE_SEND, headers=_FAKE_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    # A2A JSON-RPC response must have result with a task
    assert "result" in body, f"No 'result' in response: {body}"
    task = body["result"]
    assert task.get("status", {}).get("state") == "completed", (
        f"Task not completed: {task}"
    )


def test_executor_creates_agent_per_request(app_with_mock_agent):
    """Each POST / invocation must create a fresh agent (no shared state between requests).

    Catches regressions where PerRequestExecutor caches the agent across requests,
    which would share MCP connections and memory session state.
    """
    from starlette.testclient import TestClient

    app, mock_create, _ = app_with_mock_agent
    client = TestClient(app, raise_server_exceptions=True)

    client.post("/", json=_A2A_MESSAGE_SEND, headers=_FAKE_HEADERS)
    client.post("/", json=_A2A_MESSAGE_SEND, headers=_FAKE_HEADERS)

    assert mock_create.call_count == 2, (
        f"Expected create_agent called twice (once per request), got {mock_create.call_count}. "
        "PerRequestExecutor must not cache the agent across requests."
    )
