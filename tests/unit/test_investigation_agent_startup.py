"""
Startup smoke tests for the investigation agent (patterns/investigation-agent/).

These run in seconds locally and catch the bugs that caused 4+ failed deploys:
  1. Missing packages (fastapi) — caught by test_main_imports_without_error
  2. Agent created at startup before workload token exists — caught by
     test_ping_returns_200_without_aws_credentials and
     test_agent_not_created_at_startup

Run locally before pushing patterns/ changes:
    uv run pytest tests/unit/test_investigation_agent_startup.py -v

The tests mock AWS calls so no credentials or live services are needed.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent
# Replicate the container working directory layout (see Dockerfile):
#   COPY patterns/investigation-agent/agent.py .   → main.py, agent.py
#   COPY patterns/agui-strands-agent/tools/ tools/ → tools.gateway
#   COPY patterns/utils/ utils/                    → utils.ssm, utils.auth
_SYS_PATH_ADDITIONS = [
    # Inserted in reverse priority — last inserted goes to index 0 (highest priority)
    str(_REPO_ROOT / "patterns"),  # utils/ (lowest priority)
    str(_REPO_ROOT / "patterns" / "agui-strands-agent"),  # tools/
    str(_REPO_ROOT / "patterns" / "investigation-agent"),  # agent.py, main.py (highest)
]
_MODULES_TO_EVICT = frozenset(
    ["main", "agent", "tools", "tools.gateway", "utils", "utils.ssm", "utils.auth"]
)


@pytest.fixture(autouse=True)
def _agent_env(monkeypatch):
    """Minimum env vars for investigation agent module import (no real values needed)."""
    monkeypatch.setenv("STACK_NAME", "AgentSOAR-test")
    monkeypatch.setenv("GATEWAY_CREDENTIAL_PROVIDER_NAME", "test-provider")
    monkeypatch.setenv("AGENTCORE_RUNTIME_URL", "http://localhost:8080")
    monkeypatch.setenv("MEMORY_ID", "test-memory-id")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture(autouse=True)
def _agent_sys_path():
    """Temporarily add investigation agent dirs to sys.path, then clean up."""
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


def test_main_imports_without_error():
    """main.py must import without error.

    Catches missing packages (e.g. fastapi was missing from requirements.txt,
    causing ModuleNotFoundError in the container on every startup attempt).
    """
    with patch("utils.ssm.get_ssm_parameter", return_value="https://fake-url"):
        import main  # noqa: F401


def test_ping_returns_200_without_aws_credentials():
    """GET /ping must return 200 before any AWS call is made.

    Proves no AWS calls happen at startup — if the agent were created at module
    load time it would call @requires_access_token → 'Workload access token has
    not been set' and the container would crash before /ping ever responds.
    """
    from starlette.testclient import TestClient

    with patch("utils.ssm.get_ssm_parameter", return_value="https://fake-url"):
        import main

        client = TestClient(main.app, raise_server_exceptions=False)
        resp = client.get("/ping")
    assert resp.status_code == 200, f"/ping returned {resp.status_code}: {resp.text}"


def test_agent_not_created_at_startup():
    """create_agent() must NOT be called during module import.

    Regression: create_agent() was called at module level which triggered
    create_gateway_mcp_client() → @requires_access_token → 'Workload access
    token has not been set'. The token is only injected by AgentCore per-request;
    agent creation must be deferred to PerRequestExecutor.execute().
    """
    call_count = 0

    def _mock_create_agent(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        return MagicMock()

    with (
        patch("utils.ssm.get_ssm_parameter", return_value="https://fake-url"),
        patch("agent.create_agent", side_effect=_mock_create_agent),
    ):
        import main  # noqa: F401

    assert call_count == 0, (
        "create_agent() was called during module import. "
        "Move agent creation inside PerRequestExecutor.execute() so it only "
        "runs when a live AgentCore request context (and workload token) exists."
    )


def test_per_request_executor_is_registered():
    """build_a2a_app must be called with a PerRequestExecutor, not a bare agent.

    Ensures the app isn't accidentally wired up with a module-level agent instance.
    """
    with patch("utils.ssm.get_ssm_parameter", return_value="https://fake-url"):
        import main

    assert hasattr(main, "app"), "main.app must exist"
    assert hasattr(main, "PerRequestExecutor"), (
        "PerRequestExecutor must be defined in main"
    )
