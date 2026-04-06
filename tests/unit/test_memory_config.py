# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the AgentSOAR memory configuration in patterns/agui-strands-agent/agent.py.

These tests verify that:
- Both long-term memory namespaces are configured
- Retrieval thresholds match the intended values
- The session manager is constructed with the right config
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Stub out heavy dependencies before importing agent.py
sys.path.insert(0, "patterns/agui-strands-agent")

# ---------------------------------------------------------------------------
# Module-level stubs so agent.py imports without needing installed packages
# ---------------------------------------------------------------------------

# bedrock_agentcore stubs
_agentcore_pkg = MagicMock()

# Real RetrievalConfig and AgentCoreMemoryConfig — import from the docker image
# if available, otherwise use lightweight stand-ins so the tests still run.
try:
    from bedrock_agentcore.memory.integrations.strands.config import (
        AgentCoreMemoryConfig,
        RetrievalConfig,
    )

    _REAL_CLASSES = True
except ImportError:
    # Lightweight stand-ins that mirror the real field names
    from dataclasses import dataclass
    from typing import Dict, Optional

    @dataclass
    class RetrievalConfig:
        top_k: int = 10
        relevance_score: float = 0.2
        strategy_id: Optional[str] = None
        initialization_query: Optional[str] = None

    @dataclass
    class AgentCoreMemoryConfig:
        memory_id: str = ""
        session_id: str = ""
        actor_id: str = ""
        retrieval_config: Optional[Dict[str, RetrievalConfig]] = None
        context_tag: str = "user_context"

    _REAL_CLASSES = False

_agentcore_pkg.memory.integrations.strands.config.AgentCoreMemoryConfig = (
    AgentCoreMemoryConfig
)
_agentcore_pkg.memory.integrations.strands.config.RetrievalConfig = RetrievalConfig
_mock_session_mgr_cls = MagicMock(return_value=MagicMock())
_agentcore_pkg.memory.integrations.strands.session_manager.AgentCoreMemorySessionManager = _mock_session_mgr_cls
_agentcore_pkg.runtime.BedrockAgentCoreApp = MagicMock
_agentcore_pkg.runtime.RequestContext = MagicMock

sys.modules.setdefault("bedrock_agentcore", _agentcore_pkg)
sys.modules.setdefault("bedrock_agentcore.memory", _agentcore_pkg.memory)
sys.modules.setdefault(
    "bedrock_agentcore.memory.integrations", _agentcore_pkg.memory.integrations
)
sys.modules.setdefault(
    "bedrock_agentcore.memory.integrations.strands",
    _agentcore_pkg.memory.integrations.strands,
)
sys.modules.setdefault(
    "bedrock_agentcore.memory.integrations.strands.config",
    _agentcore_pkg.memory.integrations.strands.config,
)
sys.modules.setdefault(
    "bedrock_agentcore.memory.integrations.strands.session_manager",
    _agentcore_pkg.memory.integrations.strands.session_manager,
)
sys.modules.setdefault("bedrock_agentcore.runtime", _agentcore_pkg.runtime)

# Other stubs
for mod in [
    "ag_ui",
    "ag_ui.core",
    "ag_ui_strands",
    "strands",
    "strands.models",
    "mcp",
    "tools.gateway",
    "tools.github",
    "tools.github.strands_tools",
    "tools.code_interpreter",
    "utils.auth",
]:
    sys.modules.setdefault(mod, MagicMock())

# Now we can import the module under test
import agent  # noqa: E402  (must come after sys.modules setup)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EXPECTED_NAMESPACES = {
    "/technical-issues/{actorId}",
    "/knowledge/{actorId}",
}


def _make_session_manager(
    user_id="user-123", session_id="session-abc", memory_id="mem-001"
):
    """Call _create_session_manager with env var set, return the captured config.

    agent.py binds AgentCoreMemorySessionManager by name at import time, so we
    patch it on the agent module itself rather than on the underlying sys.modules entry.
    """
    captured = {}

    def _fake_session_manager(agentcore_memory_config, region_name):
        captured["config"] = agentcore_memory_config
        return MagicMock()

    with (
        patch.object(
            agent, "AgentCoreMemorySessionManager", side_effect=_fake_session_manager
        ),
        patch.dict(os.environ, {"MEMORY_ID": memory_id}),
    ):
        agent._create_session_manager(user_id, session_id)

    return captured["config"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMemoryNamespaces:
    def test_both_namespaces_configured(self):
        """retrieval_config must include both long-term memory namespaces."""
        config = _make_session_manager()
        assert config.retrieval_config is not None, "retrieval_config must not be None"
        configured = set(config.retrieval_config.keys())
        assert configured == EXPECTED_NAMESPACES, (
            f"Expected namespaces {EXPECTED_NAMESPACES}, got {configured}"
        )

    def test_technical_issues_threshold(self):
        """Technical-issues namespace: top_k=3, relevance_score=0.3 (tighter — avoid noise)."""
        config = _make_session_manager()
        rc = config.retrieval_config["/technical-issues/{actorId}"]
        assert rc.top_k == 3
        assert rc.relevance_score == pytest.approx(0.3)

    def test_knowledge_threshold(self):
        """Knowledge namespace: top_k=5, relevance_score=0.2 (broader — environment facts)."""
        config = _make_session_manager()
        rc = config.retrieval_config["/knowledge/{actorId}"]
        assert rc.top_k == 5
        assert rc.relevance_score == pytest.approx(0.2)

    def test_context_tag(self):
        """Injected memory context must be wrapped in the SOAR-specific XML tag."""
        config = _make_session_manager()
        assert config.context_tag == "soar-memory-context"


class TestMemoryConfigFields:
    def test_memory_id_from_env(self):
        config = _make_session_manager(memory_id="mem-xyz")
        assert config.memory_id == "mem-xyz"

    def test_actor_id_is_user_id(self):
        config = _make_session_manager(user_id="analyst-99")
        assert config.actor_id == "analyst-99"

    def test_session_id_propagated(self):
        config = _make_session_manager(session_id="thread-42")
        assert config.session_id == "thread-42"

    def test_missing_memory_id_raises(self):
        """Without MEMORY_ID env var, _create_session_manager must raise ValueError."""
        env = {k: v for k, v in os.environ.items() if k != "MEMORY_ID"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="MEMORY_ID"):
                agent._create_session_manager("u", "s")
