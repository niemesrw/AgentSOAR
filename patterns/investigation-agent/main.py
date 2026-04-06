"""A2A server entrypoint for the Investigation Agent.

The investigation agent runs as an A2A-compliant HTTP server inside an AgentCore
HTTP protocol runtime. AgentCore strips the entire /runtimes/<arn>/invocations
prefix before forwarding to the container, so the container receives requests at
root (/). We use bedrock_agentcore.runtime.a2a.build_a2a_app() which registers
the A2A JSON-RPC handler at POST / and GET /ping — the correct paths for HTTP
protocol runtimes (as opposed to AGUI runtimes which receive POST /invocations).

The agent is created per-request (inside PerRequestExecutor.execute) because
the gateway MCP client uses @requires_access_token with auth_flow='M2M', which
reads the workload access token injected by AgentCore per-request. Creating the
agent at module startup raises ValueError: Workload access token has not been set.
"""

import logging
import os

import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import UnsupportedOperationError
from a2a.utils.errors import ServerError
from agent import create_agent
from bedrock_agentcore.runtime.a2a import build_a2a_app
from strands.multiagent.a2a.executor import StrandsA2AExecutor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

stack_name = os.environ.get("STACK_NAME")
if not stack_name:
    raise RuntimeError("STACK_NAME environment variable is required")


class PerRequestExecutor(AgentExecutor):
    """Creates a fresh Strands agent per A2A invocation.

    The gateway MCP client uses @requires_access_token (auth_flow='M2M') which
    reads the workload access token injected by AgentCore per-request. This token
    is not available at module startup, so agent creation must be deferred until
    execute() is called with a live request context.
    """

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        agent = create_agent(session_id="default", actor_id="default")
        executor = StrandsA2AExecutor(agent, enable_a2a_compliant_streaming=True)
        await executor.execute(context, event_queue)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise ServerError(error=UnsupportedOperationError())


# build_a2a_app wires up:
#   POST /          — A2A JSON-RPC handler (message/send, message/stream, tasks/*)
#   GET  /ping      — AgentCore runtime healthcheck
#   GET  /.well-known/agent-card.json  — A2A agent card discovery
# It reads AGENTCORE_RUNTIME_URL (set by AgentCore) for the agent card URL.
app = build_a2a_app(PerRequestExecutor())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)  # nosec B104 — container-only; AgentCore Runtime handles external TLS/auth
