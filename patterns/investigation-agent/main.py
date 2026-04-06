"""A2A server entrypoint for the Investigation Agent.

The investigation agent runs as an A2A-compliant HTTP server inside an AgentCore
HTTP protocol runtime. AgentCore strips the entire /runtimes/<arn>/invocations
prefix before forwarding to the container, so the container receives requests at
root (/). We use bedrock_agentcore.runtime.a2a.build_a2a_app() which registers
the A2A JSON-RPC handler at POST / and GET /ping — the correct paths for HTTP
protocol runtimes (as opposed to AGUI runtimes which receive POST /invocations).
"""

import logging
import os

import uvicorn
from agent import create_agent
from bedrock_agentcore.runtime.a2a import build_a2a_app
from strands.multiagent.a2a.executor import StrandsA2AExecutor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

stack_name = os.environ.get("STACK_NAME")
if not stack_name:
    raise RuntimeError("STACK_NAME environment variable is required")

# For MVP: single agent instance with default session/actor IDs.
# A2A request headers carry the real session ID and actor ID; a future improvement
# is to subclass StrandsA2AExecutor to extract
# X-Amzn-Bedrock-AgentCore-Runtime-Session-Id and
# X-Amzn-Bedrock-AgentCore-Runtime-Custom-Actorid from the BedrockCallContextBuilder
# and pass them to create_agent() for proper per-request isolation.
agent = create_agent(session_id="default", actor_id="default")
executor = StrandsA2AExecutor(agent, enable_a2a_compliant_streaming=True)

# build_a2a_app wires up:
#   POST /          — A2A JSON-RPC handler (message/send, message/stream, tasks/*)
#   GET  /ping      — AgentCore runtime healthcheck
#   GET  /.well-known/agent-card.json  — A2A agent card discovery
# It reads AGENTCORE_RUNTIME_URL (set by AgentCore) for the agent card URL.
app = build_a2a_app(executor)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)  # nosec B104 — container-only; AgentCore Runtime handles external TLS/auth
