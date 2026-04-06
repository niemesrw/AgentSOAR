"""A2A server entrypoint for the Investigation Agent.

The investigation agent runs as an A2A-compliant HTTP server. AgentCore Runtime
handles TLS termination and JWT auth — this process only sees authenticated requests.
"""

import logging
import os

import uvicorn
from agent import create_agent
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

server = A2AServer(
    agent=agent,
    http_url=RUNTIME_INVOCATIONS_URL,
    enable_a2a_compliant_streaming=True,
)

app = server.to_starlette_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)  # nosec B104 — container-only; AgentCore Runtime handles external TLS/auth
