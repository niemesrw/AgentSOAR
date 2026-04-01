"""AG-UI Strands agent with Gateway MCP tools, Memory, and Code Interpreter.

Uses ag-ui-strands to produce native AG-UI SSE events.
AgentCore proxies these unchanged when deployed with --protocol AGUI.
"""

import logging
import os

import boto3
from ag_ui.core import RunAgentInput, RunErrorEvent
from ag_ui_strands import StrandsAgent
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from bedrock_agentcore.runtime import BedrockAgentCoreApp, RequestContext
from strands import Agent, tool
from strands.models import BedrockModel
from tools.gateway import create_gateway_mcp_client
from utils.auth import extract_user_id_from_context

from tools.code_interpreter import StrandsCodeInterpreterTools

logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

SYSTEM_PROMPT = (
    "You are a helpful security assistant (SOAR agent) with access to tools via the Gateway "
    "and Code Interpreter. You can help with security investigations, create GitHub issues "
    "for incidents, and run code to analyze data. "
    "If GitHub tools fail with an authorization error, ask the user to run 'github_connect' first. "
    "When asked about your tools, list them and explain what they do."
)


def _make_github_connect_tool(user_jwt: str):
    """Return a github_connect @tool that uses the caller's JWT for user-delegated OAuth."""

    @tool
    def github_connect() -> str:
        """
        Connect your GitHub account to the SOAR agent.

        Call this tool once to authorize GitHub access. You will receive a URL to visit
        in your browser. After you authorize the GitHub OAuth App, your token is stored
        securely in AgentCore Identity and GitHub tools will work automatically.

        If you are already connected, this returns a confirmation message.
        """
        provider_name = os.environ.get("GITHUB_CREDENTIAL_PROVIDER_NAME", "")
        if not provider_name:
            return "GITHUB_CREDENTIAL_PROVIDER_NAME is not configured. Contact your administrator."

        client = boto3.client("bedrock-agentcore")

        # Step 1: Get a workload access token for this user's session.
        # AgentCore appends a random suffix to the runtime name (e.g. AgentSOAR_FASTAgent-SgcWYdHjn4).
        # We can't inject the runtime ARN as an env var (circular CFN dependency), so we read it from
        # the SSM parameter that CDK writes at deploy time: /{STACK_NAME}/runtime-arn.
        # For user-delegated OAuth flows we must use get_workload_access_token_for_jwt and pass the
        # caller's Cognito JWT; get_workload_access_token is only for non-user M2M flows.
        stack_name = os.environ.get("STACK_NAME", "")
        try:
            ssm = boto3.client("ssm")
            runtime_arn = ssm.get_parameter(Name=f"/{stack_name}/runtime-arn")[
                "Parameter"
            ]["Value"]
            workload_name = runtime_arn.split("/")[-1]
        except Exception as e:
            logger.warning("Could not read runtime ARN from SSM: %s", e)
            workload_name = ""
        try:
            workload_resp = client.get_workload_access_token_for_jwt(
                workloadName=workload_name,
                userToken=user_jwt,
            )
            workload_token = workload_resp["workloadAccessToken"]
        except Exception as e:
            logger.error(
                "get_workload_access_token_for_jwt failed (workloadName=%s): %s",
                workload_name,
                e,
            )
            return f"Could not get workload token: {e}. Ensure the agent runtime is deployed correctly."

        # Step 2: Try to get the user's GitHub token.
        # - If the token exists in the vault → user is already connected.
        # - If AgentCore returns an authorizationUrl → user needs to authorize.
        try:
            token_resp = client.get_resource_oauth2_token(
                resourceCredentialProviderName=provider_name,
                oauth2Flow="USER_FEDERATION",
                workloadIdentityToken=workload_token,
                scopes=["repo", "read:user"],
            )
        except Exception as e:
            logger.error("get_resource_oauth2_token error: %s", e)
            return f"Error checking GitHub authorization: {e}"

        if "accessToken" in token_resp:
            return (
                "GitHub is already connected! Your GitHub tools are ready to use. "
                "Try: 'list my open issues in owner/repo'"
            )

        if "authorizationUrl" in token_resp:
            auth_url = token_resp["authorizationUrl"]
            return (
                f"Please authorize GitHub access by visiting this URL in your browser:\n\n"
                f"{auth_url}\n\n"
                f"After you click 'Authorize', your GitHub token will be stored securely "
                f"and GitHub tools will work automatically in future requests."
            )

        return f"Unexpected response from AgentCore Identity: {list(token_resp.keys())}"

    return github_connect


def _build_model() -> BedrockModel:
    return BedrockModel(model_id="global.anthropic.claude-sonnet-4-6", temperature=0.1)


def _create_session_manager(
    user_id: str, session_id: str
) -> AgentCoreMemorySessionManager:
    memory_id = os.environ.get("MEMORY_ID")
    if not memory_id:
        raise ValueError("MEMORY_ID environment variable is required")
    config = AgentCoreMemoryConfig(
        memory_id=memory_id, session_id=session_id, actor_id=user_id
    )
    return AgentCoreMemorySessionManager(
        agentcore_memory_config=config,
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )


def _create_agent(user_id: str, session_id: str, user_jwt: str) -> Agent:
    """Create a Strands Agent with Gateway MCP tools, Memory, and Code Interpreter."""
    gateway_client = create_gateway_mcp_client()

    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    code_tools = StrandsCodeInterpreterTools(region)

    return Agent(
        name="strands_agent",
        system_prompt=SYSTEM_PROMPT,
        tools=[
            gateway_client,
            code_tools.execute_python_securely,
            _make_github_connect_tool(user_jwt),
        ],
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
        user_jwt: str,
        name: str,
        description: str,
    ):
        self._user_id = user_id
        self._session_id = session_id
        self._user_jwt = user_jwt
        super().__init__(
            agent=Agent(model=_build_model(), system_prompt=SYSTEM_PROMPT),
            name=name,
            description=description,
        )

    async def run(self, input_data: RunAgentInput):
        thread_id = input_data.thread_id or "default"
        self._agents_by_thread[thread_id] = _create_agent(
            self._user_id, self._session_id, self._user_jwt
        )
        async for event in super().run(input_data):
            yield event


def _extract_jwt(context: RequestContext) -> str:
    """Return the raw JWT from the Authorization header (strips 'Bearer ' prefix)."""
    auth = (context.request_headers or {}).get("Authorization", "")
    return auth[len("Bearer ") :] if auth.startswith("Bearer ") else auth


@app.entrypoint
async def invocations(payload: dict, context: RequestContext):
    input_data = RunAgentInput.model_validate(payload)
    user_id = extract_user_id_from_context(context)
    user_jwt = _extract_jwt(context)

    agent = ActorAwareStrandsAgent(
        user_id=user_id,
        session_id=input_data.thread_id,
        user_jwt=user_jwt,
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
