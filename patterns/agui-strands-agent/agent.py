"""AG-UI Strands agent with Gateway MCP tools, Memory, and Code Interpreter.

Uses ag-ui-strands to produce native AG-UI SSE events.
AgentCore proxies these unchanged when deployed with --protocol AGUI.
"""

import logging
import os

import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from ag_ui.core import RunAgentInput, RunErrorEvent
from ag_ui_strands import StrandsAgent
from bedrock_agentcore.identity.auth import requires_access_token
from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
    RetrievalConfig,
)
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from bedrock_agentcore.runtime import (
    BedrockAgentCoreApp,
    RequestContext,
)
from strands import Agent, tool
from strands.agent.a2a_agent import A2AAgent
from strands.models import BedrockModel
from tools.gateway import create_gateway_mcp_client
from tools.github.strands_tools import make_github_tools
from utils.auth import extract_user_id_from_context
from utils.ssm import get_ssm_parameter

from tools.code_interpreter import StrandsCodeInterpreterTools

logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

SYSTEM_PROMPT = (
    "You are a security analyst agent (SOAR) with access to gateway tools and a code interpreter. "
    "Your role is to investigate security alerts and findings, triage incidents, query data sources, "
    "run analysis code, and create GitHub issues to track remediation. "
    "Think like a tier-2 SOC analyst: be precise, cite evidence, and recommend concrete next steps. "
    "If GitHub tools fail with an authorization error, prompt the user to call github_connect first — "
    "it uses device authorization (visit a URL, enter a code, then confirm). "
    "Each user has their own independent GitHub connection.\n\n"
    "MANDATORY TOOL ROUTING — follow this exactly, no exceptions:\n"
    "- When given a GuardDuty finding ID to investigate: call investigate_finding FIRST. "
    "Do NOT call guardduty or cloudtrail tools directly. investigate_finding delegates to a "
    "specialist sub-agent that does the GuardDuty lookup, CloudTrail correlation, timeline, "
    "and blast radius assessment internally. Prior memory context does not change this — "
    "always delegate finding investigations to investigate_finding regardless of what memory says.\n"
    "- For quick lookups only (e.g. 'list findings', 'how many open findings'): "
    "use the gateway tools directly.\n\n"
    "You have persistent cross-session memory. Before each message, relevant facts from prior "
    "investigations are automatically retrieved and injected as <soar-memory-context> in your prompt. "
    "This context may include: past incident triage conclusions, known threat actor IPs and principals, "
    "confirmed false positives, known-good baselines (pen test vendors, authorized automation), and "
    "facts about the AWS environment. When <soar-memory-context> is present, treat it as verified "
    "prior context and reference it in your reasoning. Your responses are also saved to memory "
    "automatically, so future sessions benefit from what you learn today. "
    "If no memory context appears, either no relevant prior incidents exist yet or the namespaces "
    "are still being populated from recent sessions."
)


def _build_model() -> BedrockModel:
    return BedrockModel(model_id="global.anthropic.claude-sonnet-4-6", temperature=0.1)


def _create_session_manager(
    user_id: str, session_id: str
) -> AgentCoreMemorySessionManager:
    memory_id = os.environ.get("MEMORY_ID")
    if not memory_id:
        raise ValueError("MEMORY_ID environment variable is required")
    config = AgentCoreMemoryConfig(
        memory_id=memory_id,
        session_id=session_id,
        actor_id=user_id,
        # Search long-term memory namespaces before each user message.
        # The session manager's MessageAddedEvent hook retrieves relevant records
        # from both namespaces and injects them as <soar-memory-context> in the prompt.
        # Namespaces are populated by the CustomMemoryStrategy / SemanticMemoryStrategy
        # configured on the Memory resource in backend-stack.ts.
        retrieval_config={
            "/technical-issues/{actorId}": RetrievalConfig(
                top_k=3, relevance_score=0.3
            ),
            "/knowledge/{actorId}": RetrievalConfig(top_k=5, relevance_score=0.2),
        },
        context_tag="soar-memory-context",
    )
    return AgentCoreMemorySessionManager(
        agentcore_memory_config=config,
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )


def _make_investigation_tool(session_id: str):
    """Create the investigate_finding Strands tool bound to this session.

    Discovers the investigation agent URL from SSM (/{STACK_NAME}/investigation-agent-url).
    Returns None if the SSM parameter doesn't exist yet (graceful degradation during rollout
    — the orchestrator works before the investigation agent is deployed).
    Raises if SSM lookup fails for any other reason (permissions, network, etc.).
    """
    stack_name = os.environ.get("STACK_NAME")
    investigation_agent_url = None
    if stack_name:
        ssm_param = f"/{stack_name}/investigation-agent-url"
        try:
            investigation_agent_url = get_ssm_parameter(ssm_param)
        except ValueError as exc:
            if "not found" in str(exc).lower():
                logger.info(
                    "SSM parameter %s not found; investigation tool disabled (agent not yet deployed)",
                    ssm_param,
                )
            else:
                logger.exception(
                    "Failed to retrieve SSM parameter %s for investigation tool",
                    ssm_param,
                )
                raise

    if not investigation_agent_url:
        return None

    provider = os.environ.get("GATEWAY_CREDENTIAL_PROVIDER_NAME")
    if not provider:
        logger.warning(
            "GATEWAY_CREDENTIAL_PROVIDER_NAME not set; skipping investigation tool"
        )
        return None

    @requires_access_token(provider_name=provider, auth_flow="M2M", scopes=[])
    def _get_token(access_token: str = None):
        return access_token

    # Capture these in the closure; the actual token is fetched fresh per invocation
    # inside investigate_finding() to avoid stale-token failures after expiry.
    _endpoint = investigation_agent_url
    _session_id = session_id

    @tool
    def investigate_finding(
        finding_id: str, account_id: str, region: str = "us-east-1"
    ) -> str:
        """Perform a deep investigation of a GuardDuty finding.
        Correlates with CloudTrail events, builds a timeline, and assesses blast radius.
        Use this for thorough analysis instead of calling GuardDuty/CloudTrail tools directly."""
        # Fresh token + client per call — Cognito M2M tokens expire (~1 hr);
        # creating per-invocation ensures we never use a stale credential.
        token = _get_token()
        client_factory = ClientFactory(
            ClientConfig(
                httpx_client=httpx.AsyncClient(
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": _session_id,
                    },
                    timeout=120,
                ),
                streaming=True,
            )
        )
        remote_agent = A2AAgent(
            endpoint=_endpoint,
            name="investigation_agent",
            description="Deep incident investigation specialist",
            a2a_client_factory=client_factory,
        )
        # Pre-seed the agent card so A2AAgent skips its unauthenticated GET to
        # /.well-known/agent-card.json — AgentCore Runtime rejects that request
        # (requires JWT on all endpoints) causing a 404 before any work is done.
        remote_agent._agent_card = AgentCard(
            name="investigation_agent",
            description="Deep incident investigation specialist",
            url=_endpoint,
            version="1.0.0",
            capabilities=AgentCapabilities(streaming=True),
            defaultInputModes=["text"],
            defaultOutputModes=["text"],
            skills=[
                AgentSkill(
                    id="investigate",
                    name="Investigate GuardDuty finding",
                    description="Deep GuardDuty + CloudTrail correlation, timeline, blast radius",
                    tags=[],
                )
            ],
        )
        return str(
            remote_agent(
                f"Investigate GuardDuty finding {finding_id} in account {account_id} region {region}. "
                "Correlate with CloudTrail, build an event timeline, and assess blast radius."
            )
        )

    return investigate_finding


def _create_agent(user_id: str, session_id: str) -> Agent:
    """Create a Strands Agent with Gateway MCP tools, GitHub tools, Memory, and Code Interpreter."""
    gateway_client = create_gateway_mcp_client()

    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    code_tools = StrandsCodeInterpreterTools(region)

    tools = [
        gateway_client,
        code_tools.execute_python_securely,
        *make_github_tools(user_id),
    ]

    investigation_tool = _make_investigation_tool(session_id)
    if investigation_tool:
        tools.append(investigation_tool)

    return Agent(
        name="strands_agent",
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
        model=_build_model(),
        session_manager=_create_session_manager(user_id, session_id),
    )


class ActorAwareStrandsAgent(StrandsAgent):
    """StrandsAgent that creates the agent per-request with fresh MCP context."""

    def __init__(
        self,
        *,
        user_id: str,
        session_id: str,
        name: str,
        description: str,
    ):
        self._user_id = user_id
        self._session_id = session_id
        super().__init__(
            agent=Agent(model=_build_model(), system_prompt=SYSTEM_PROMPT),
            name=name,
            description=description,
        )

    async def run(self, input_data: RunAgentInput):
        thread_id = input_data.thread_id or "default"
        self._agents_by_thread[thread_id] = _create_agent(
            self._user_id, self._session_id
        )
        async for event in super().run(input_data):
            yield event


@app.entrypoint
async def invocations(payload: dict, context: RequestContext):
    input_data = RunAgentInput.model_validate(payload)
    user_id = extract_user_id_from_context(context)

    agent = ActorAwareStrandsAgent(
        user_id=user_id,
        session_id=input_data.thread_id,
        name="agui_strands_agent",
        description="AG-UI Strands agent with Gateway MCP tools and Code Interpreter",
    )

    try:
        async for event in agent.run(input_data):
            if event is not None:
                yield event.model_dump(mode="json", by_alias=True, exclude_none=True)
    except Exception as exc:
        logger.exception("Agent run failed")
        yield RunErrorEvent(
            message=str(exc) or type(exc).__name__,
            code=type(exc).__name__,
        ).model_dump(mode="json", by_alias=True, exclude_none=True)


if __name__ == "__main__":
    app.run()
