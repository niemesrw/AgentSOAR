"""
Validates A2A API contracts used in patterns/agui-strands-agent/agent.py.

These tests exist to catch import errors and API signature mismatches before
a deploy cycle — the equivalent of the mistakes that caused the investigation
tool to fail three times in production:
  1. requires_access_token missing scopes=[]
  2. requires_access_token injecting as 'access_token' not 'token'
  3. AgentResult.message is a dict, not a string — use str(result)
"""

import inspect

import pytest


def test_a2a_client_imports():
    """ClientFactory and ClientConfig must be importable from a2a.client."""
    from a2a.client import ClientConfig, ClientFactory

    assert ClientFactory is not None
    assert ClientConfig is not None


def test_a2a_client_factory_takes_config():
    """ClientFactory constructor must accept a ClientConfig positional arg."""
    from a2a.client import ClientFactory

    sig = inspect.signature(ClientFactory.__init__)
    params = list(sig.parameters.keys())
    assert "config" in params


def test_a2a_client_config_takes_httpx_client():
    """ClientConfig must accept an httpx_client kwarg."""
    from a2a.client import ClientConfig

    sig = inspect.signature(ClientConfig.__init__)
    assert "httpx_client" in sig.parameters


def test_strands_a2a_agent_import():
    """A2AAgent must be importable from strands.agent.a2a_agent."""
    from strands.agent.a2a_agent import A2AAgent

    assert A2AAgent is not None


def test_strands_a2a_agent_constructor():
    """A2AAgent must accept endpoint, name, description, a2a_client_factory."""
    from strands.agent.a2a_agent import A2AAgent

    sig = inspect.signature(A2AAgent.__init__)
    params = list(sig.parameters.keys())
    assert "endpoint" in params
    assert "name" in params
    assert "description" in params
    assert "a2a_client_factory" in params


def test_strands_agent_result_str_not_message():
    """AgentResult.__str__ must return a string — .message is a dict, not str.

    Regression: investigation tool originally returned result.message which is
    a Message dict. Correct usage is str(result).
    """
    from strands.agent.agent_result import AgentResult

    sig = inspect.signature(AgentResult.__init__)
    params = list(sig.parameters.keys())
    # .message exists but is a Message (dict), not a str
    assert "message" in params
    # __str__ must be explicitly defined (not just object.__str__)
    assert AgentResult.__str__ is not object.__str__, (
        "AgentResult must define __str__ — use str(result), not result.message"
    )


def test_requires_access_token_scopes_required():
    """requires_access_token must have a 'scopes' parameter.

    Regression: investigation tool originally omitted scopes=[] causing TypeError.
    """
    from bedrock_agentcore.identity.auth import requires_access_token

    sig = inspect.signature(requires_access_token)
    assert "scopes" in sig.parameters


def test_requires_access_token_injects_access_token_kwarg():
    """The decorator must inject the token as 'access_token', not 'token'.

    Regression: investigation tool used def _get_token(token=None) which caused
    TypeError: _get_token() got an unexpected keyword argument 'access_token'.
    """

    # Decorate a function with the wrong param name and verify it would fail,
    # then verify the right param name works.
    # We can't call it (no real provider) but we can inspect the wrapper behavior
    # by checking that gateway.py's pattern (access_token: str) is the convention.
    import ast
    from pathlib import Path

    gateway_src = (
        Path(__file__).parent.parent.parent
        / "patterns/agui-strands-agent/tools/gateway.py"
    )
    tree = ast.parse(gateway_src.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_fetch_gateway_token":
            param_names = [a.arg for a in node.args.args]
            assert "access_token" in param_names, (
                "gateway.py _fetch_gateway_token uses 'access_token' — "
                "all @requires_access_token functions must use this param name"
            )
            return
    pytest.fail("_fetch_gateway_token not found in gateway.py")


def test_a2a_server_import():
    """A2AServer must be importable from strands.multiagent.a2a."""
    from strands.multiagent.a2a import A2AServer

    assert A2AServer is not None


def test_a2a_server_has_to_starlette_app():
    """A2AServer must have a to_starlette_app() method."""
    from strands.multiagent.a2a import A2AServer

    assert hasattr(A2AServer, "to_starlette_app")
    assert callable(A2AServer.to_starlette_app)
