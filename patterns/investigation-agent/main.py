"""A2A server entrypoint for the Investigation Agent.

The investigation agent runs as an A2A-compliant HTTP server. AgentCore Runtime
handles TLS termination and JWT auth — this process only sees authenticated requests.

Path routing: AgentCore strips /runtimes/<arn> from the external URL before forwarding
to the container, so the container receives POST /invocations. We register the A2A
JSON-RPC handler at /invocations to match, mirroring how BedrockAgentCoreApp works.
"""

import logging
import os

import uvicorn
from a2a.server.apps import A2AStarletteApplication
from agent import create_agent
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from strands.multiagent.a2a import A2AServer
from utils.ssm import get_ssm_parameter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Construct the runtime invocations URL from SSM.
# We store the ARN in SSM during CDK deployment and build the URL here to avoid
# the circular dependency that would result from passing the URL as an env var
# (the ARN isn't known until after the runtime is created).
stack_name = os.environ.get("STACK_NAME")
if not stack_name:
    raise RuntimeError("STACK_NAME environment variable is required")

region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
runtime_arn = get_ssm_parameter(f"/{stack_name}/investigation-agent-runtime-arn")
RUNTIME_INVOCATIONS_URL = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{runtime_arn}/invocations"
logger.info("Investigation agent A2A URL: %s", RUNTIME_INVOCATIONS_URL)

# For MVP: single agent instance with default session/actor IDs.
# A2A request headers carry the real session ID and actor ID; a future improvement
# is to subclass StrandsA2AExecutor to extract
# X-Amzn-Bedrock-AgentCore-Runtime-Session-Id and
# X-Amzn-Bedrock-AgentCore-Runtime-Custom-Actorid from the RequestContext
# and pass them to create_agent() for proper per-request isolation.
agent = create_agent(session_id="default", actor_id="default")

# Build the A2A server — http_url sets the URL advertised in the agent card.
server = A2AServer(
    agent=agent,
    http_url=RUNTIME_INVOCATIONS_URL,
    enable_a2a_compliant_streaming=True,
)

# AgentCore strips /runtimes/<arn> before forwarding to the container, so requests
# arrive at /invocations (not the full ARN path). Register the JSON-RPC handler there.
# We build routes manually instead of using server.to_starlette_app() to avoid the
# default path-based mounting which would register at the full ARN path incorrectly.
a2a_routes = A2AStarletteApplication(
    agent_card=server.public_agent_card,
    http_handler=server.request_handler,
).routes(rpc_url="/invocations")


async def _ping(request: Request) -> PlainTextResponse:
    return PlainTextResponse("pong")


app = Starlette(
    routes=[
        Route("/ping", _ping, methods=["GET"]),
        *a2a_routes,
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)  # nosec B104 — container-only; AgentCore Runtime handles external TLS/auth
