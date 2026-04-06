"""Investigation Agent — deep GuardDuty + CloudTrail incident analysis via A2A."""

import logging
import os

from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
    RetrievalConfig,
)
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from strands import Agent
from strands.models import BedrockModel
from tools.gateway import create_gateway_mcp_client

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a specialist security investigation agent for a cloud SOAR platform.
You receive GuardDuty finding IDs and perform deep incident analysis:

1. Retrieve the full finding details (severity, type, affected resource, threat actor)
2. Pull correlated CloudTrail events for the affected resource and the actor principal
3. Build a chronological event timeline showing what the actor did before, during, and after the finding
4. Assess blast radius — what other resources could be affected? Any lateral movement indicators?
5. Return a structured investigation report with clear evidence citations

Be precise. Cite evidence. Include finding IDs, resource ARNs, timestamps, and API call sequences.
If prior investigation context is available in <soar-memory-context>, reference it — it may contain
known-good baselines, previously confirmed false positives, or threat actor patterns from past incidents.
"""


def create_agent(session_id: str, actor_id: str) -> Agent:
    """Create a Strands Agent for incident investigation with memory hooks."""
    memory_id = os.environ.get("MEMORY_ID")
    if not memory_id:
        raise ValueError("MEMORY_ID environment variable is required")

    config = AgentCoreMemoryConfig(
        memory_id=memory_id,
        session_id=session_id,
        actor_id=actor_id,
        retrieval_config={
            "/technical-issues/{actorId}": RetrievalConfig(
                top_k=3, relevance_score=0.3
            ),
            "/knowledge/{actorId}": RetrievalConfig(top_k=5, relevance_score=0.2),
        },
        context_tag="soar-memory-context",
    )
    session_manager = AgentCoreMemorySessionManager(
        agentcore_memory_config=config,
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )

    return Agent(
        name="investigation_agent",
        description="Deep GuardDuty + CloudTrail incident investigation specialist",
        system_prompt=SYSTEM_PROMPT,
        model=BedrockModel(
            model_id="us.anthropic.claude-sonnet-4-6-20251101-v1:0",
            temperature=0.1,
        ),
        tools=[create_gateway_mcp_client()],
        session_manager=session_manager,
    )
