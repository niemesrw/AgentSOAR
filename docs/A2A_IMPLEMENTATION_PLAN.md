# A2A Multi-Agent Implementation Plan

This document captures everything needed to implement Part 2 of the memory/A2A work.
Pick it up in a fresh session — start by reading this file and the files listed in "Read first".

## What's already done

- Long-term memory hooks deployed (CustomMemory + SemanticMemory strategies in CDK)
- `retrieval_config` wired in `agent.py` — session manager already searches both namespaces
- Blog post Part 4 drafted (memory section accurate, A2A section is forward-looking)

## What we're building

Investigation Agent as the first A2A sub-agent. It receives a finding ID + context from
the orchestrator, runs deep GuardDuty + CloudTrail correlation, and streams results back.

```
User → Orchestrator (agui-strands-agent)
             │
             └──► Investigation Agent  ← A2A over HTTPS → AgentCore Runtime
                  - GuardDuty tools
                  - CloudTrail tools
                  - Same memory hooks
```

Playbook and Notification agents come later. Investigation agent first.

---

## Read first (before writing any code)

```
patterns/agui-strands-agent/agent.py          # orchestrator — needs A2AAgent added
gateway/tools/guardduty_tool/                 # tools that move to investigation agent
gateway/tools/cloudtrail_tool/                # tools that move to investigation agent
infra-cdk/lib/backend-stack.ts                # CDK pattern — investigation agent stack mirrors this
infra-cdk/lib/fast-main-stack.ts              # where to add new nested stack
infra-cdk/lib/cognito-stack.ts                # where to add new Cognito scope
```

---

## Key library facts (already verified in Docker image)

Strands 1.16.0 has native A2A support — no extra packages needed beyond `a2a-sdk`.

**Sub-agent server side** (`strands.multiagent.a2a`):
```python
from strands.multiagent.a2a import A2AServer, StrandsA2AExecutor

server = A2AServer(
    agent=strands_agent,
    http_url="https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<arn>/invocations",
    enable_a2a_compliant_streaming=True,   # IMPORTANT: set this
)
app = server.to_starlette_app()   # pass to uvicorn
```

**Orchestrator client side** (`strands.agent.a2a_agent`):
```python
from strands.agent.a2a_agent import A2AAgent
from a2a.client import ClientFactory, ClientConfig

investigation_agent = A2AAgent(
    endpoint="https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<arn>/invocations",
    name="investigation_agent",
    description="Deep GuardDuty + CloudTrail incident investigation",
    a2a_client_factory=...,   # see auth section below
)

# Use as a tool by calling it — A2AAgent is callable
result = investigation_agent("Investigate finding abc123 in account 429971481640 region us-east-1")
```

To use `A2AAgent` as a Strands tool, wrap it with `@tool`:
```python
from strands import tool

@tool
def investigate_finding(finding_id: str, account_id: str, region: str) -> str:
    """Deep investigation of a GuardDuty finding using CloudTrail correlation.
    Use this for thorough analysis — correlates the finding with CloudTrail events,
    builds an event timeline, and assesses blast radius."""
    return investigation_agent(
        f"Investigate GuardDuty finding {finding_id} in account {account_id} region {region}. "
        "Correlate with CloudTrail, build a timeline, assess blast radius."
    ).message
```

---

## M2M auth between orchestrator and investigation agent

The investigation agent's AgentCore Runtime uses JWT auth (same as the main agent).
The orchestrator needs a bearer token to call it.

```python
from bedrock_agentcore.identity.auth import requires_access_token
from a2a.client import ClientFactory, ClientConfig
import httpx

INVESTIGATION_AGENT_PROVIDER = os.environ["INVESTIGATION_AGENT_PROVIDER"]

def _make_investigation_client_factory(session_id: str) -> ClientFactory:
    """Create A2A ClientFactory with M2M bearer token."""

    @requires_access_token(provider_name=INVESTIGATION_AGENT_PROVIDER, auth_flow="M2M")
    def _get_token(token: str = None):
        return token

    token = _get_token()
    httpx_client = httpx.AsyncClient(
        headers={
            "Authorization": f"Bearer {token}",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
        },
        timeout=120,
    )
    return ClientFactory(ClientConfig(httpx_client=httpx_client, streaming=True))
```

`INVESTIGATION_AGENT_PROVIDER` is the Cognito credential provider name — set as env var
on the orchestrator runtime, stored in SSM, same pattern as `GATEWAY_PROVIDER_NAME`.

---

## Files to create

### 1. `patterns/investigation-agent/agent.py`

```python
"""Investigation Agent — deep GuardDuty + CloudTrail incident analysis via A2A."""

import logging
import os

from strands import Agent
from strands.models import BedrockModel
from strands.multiagent.a2a import A2AServer
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig, RetrievalConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager
from tools.gateway import create_gateway_mcp_client

SYSTEM_PROMPT = """You are a specialist security investigation agent.
You receive GuardDuty finding IDs and perform deep analysis:
1. Retrieve full finding details
2. Pull correlated CloudTrail events for the affected resource and actor
3. Build a chronological event timeline
4. Assess blast radius — what else could be affected?
5. Return a structured investigation report

Be precise. Cite evidence. Include finding IDs, resource ARNs, timestamps, and API calls."""

def create_agent(session_id: str, actor_id: str) -> Agent:
    memory_id = os.environ["MEMORY_ID"]
    config = AgentCoreMemoryConfig(
        memory_id=memory_id,
        session_id=session_id,
        actor_id=actor_id,
        retrieval_config={
            "/technical-issues/{actorId}": RetrievalConfig(top_k=3, relevance_score=0.3),
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
        model=BedrockModel(model_id="us.anthropic.claude-sonnet-4-6-20251101-v1:0", temperature=0.1),
        tools=[create_gateway_mcp_client()],
        session_manager=session_manager,
    )
```

### 2. `patterns/investigation-agent/main.py`

```python
"""A2A server entrypoint for the Investigation Agent."""

import logging
import os
import uvicorn
from agent import create_agent
from strands.multiagent.a2a import A2AServer

logging.basicConfig(level=logging.INFO)

RUNTIME_INVOCATIONS_URL = os.environ["RUNTIME_INVOCATIONS_URL"]

# A2A agents are stateless per-request — create fresh agent per request
# by making the executor create the agent on each invocation.
# For now: single agent instance (session_id/actor_id from A2A context — see executor subclass)
agent = create_agent(session_id="default", actor_id="default")

server = A2AServer(
    agent=agent,
    http_url=RUNTIME_INVOCATIONS_URL,
    enable_a2a_compliant_streaming=True,
)

app = server.to_starlette_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
```

**Note:** The A2A request context carries session/actor IDs in headers. The executor
receives these via `context.message` metadata. For MVP, `default` session is fine.
For proper per-request session isolation, subclass `StrandsA2AExecutor` to extract
`X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` and `X-Amzn-Bedrock-AgentCore-Runtime-Custom-Actorid`
from the A2A `RequestContext` and pass them to `create_agent()`.

### 3. `patterns/investigation-agent/Dockerfile`

Copy from `patterns/agui-strands-agent/Dockerfile` — same base image and structure.
Change the CMD to `uvicorn main:app --host 0.0.0.0 --port 8080`.

### 4. `patterns/investigation-agent/requirements.txt`

```
strands-agents==1.16.0
bedrock-agentcore[strands-agents]==1.0.6
a2a-sdk==0.3.24
uvicorn>=0.30.0
mcp==1.21.0
```

Same versions as the main agent. Check `uv.lock` for exact pins.

---

## Files to modify

### 5. `infra-cdk/lib/investigation-agent-stack.ts` (NEW)

New nested stack. Mirror `backend-stack.ts` but simpler — no Gateway, no Cognito user auth,
no Feedback API. Just:
- IAM role (AgentCoreRole + GuardDuty/CloudTrail cross-account permissions)
- ECR asset + AgentCore Runtime with `protocolConfiguration: { serverProtocol: "HTTP" }`
- SSM parameters for: runtime ARN, invocations URL, credential provider name
- AgentCore Memory (can share the same memory ID as the main agent — pass it as a prop)

Key CDK runtime config:
```typescript
const runtime = new agentcore.Runtime(this, "InvestigationAgentRuntime", {
  agentRuntimeName: `${stackName}-investigation`,
  agentRuntimeArtifact: agentcore.AgentRuntimeArtifact.fromImage(image),
  roleArn: agentRole.roleArn,
  networkConfiguration: { networkMode: "PUBLIC" },
  protocolConfiguration: { serverProtocol: "HTTP" },   // NOT AGUI
  authorizerConfiguration: {
    customJwtAuthorizer: {
      discoveryUrl: cognitoDiscoveryUrl,
      allowedClients: [machineClientId],   // M2M client from CognitoStack
    }
  },
  environmentVariables: {
    MEMORY_ID: memoryId,
    RUNTIME_INVOCATIONS_URL: `https://bedrock-agentcore.${region}.amazonaws.com/runtimes/${runtimeArn}/invocations`,
    GATEWAY_PROVIDER_NAME: gatewayProviderName,
    AWS_DEFAULT_REGION: region,
  },
})
```

The runtime ARN is a circular dependency (you need the ARN to set `RUNTIME_INVOCATIONS_URL`
as an env var, but the ARN doesn't exist until after deploy). Work around:
- Use a placeholder and update via SSM + a post-deploy Lambda, OR
- Use a CDK custom resource, OR
- Simpler: store the runtime ARN in SSM, have `main.py` fetch it at startup via `boto3.ssm.get_parameter()`
  and construct the URL itself. The agent knows its own region.

**Recommended:** Have `main.py` construct its own URL:
```python
import boto3
STACK_NAME = os.environ["STACK_NAME"]
ssm = boto3.client("ssm")
runtime_arn = ssm.get_parameter(Name=f"/{STACK_NAME}/investigation-agent-runtime-arn")["Parameter"]["Value"]
region = os.environ["AWS_DEFAULT_REGION"]
RUNTIME_INVOCATIONS_URL = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{runtime_arn}/invocations"
```

### 6. `infra-cdk/lib/fast-main-stack.ts`

Add `InvestigationAgentStack` as a nested stack after `BackendStack`. Pass:
- `memoryArn` from BackendStack
- `userPoolId`, `machineClientId` from CognitoStack
- `gatewayProviderName` from BackendStack

### 7. `infra-cdk/lib/cognito-stack.ts`

Add a new Cognito machine client scoped to the investigation agent, OR reuse the existing
machine client and add a new resource server scope. The simplest approach:

Add one SSM parameter: `/{stackName}/investigation-agent-provider` pointing to
the credential provider the orchestrator uses to get tokens for calling the investigation agent.

The credential provider is created by the AgentCore Runtime registration automatically
(same pattern as `GATEWAY_PROVIDER_NAME`). Check how `GATEWAY_PROVIDER_NAME` is set
in `backend-stack.ts` and mirror that pattern.

### 8. `patterns/agui-strands-agent/agent.py`

Add the `investigate_finding` tool and wire the `A2AAgent`:

```python
from strands.agent.a2a_agent import A2AAgent
from a2a.client import ClientFactory, ClientConfig
from bedrock_agentcore.identity.auth import requires_access_token

def _make_investigation_tool(session_id: str):
    """Create the investigate_finding Strands tool bound to this session."""
    endpoint = os.environ.get("INVESTIGATION_AGENT_URL")
    if not endpoint:
        return None   # investigation agent not deployed yet — graceful degradation

    provider = os.environ["INVESTIGATION_AGENT_PROVIDER"]

    @requires_access_token(provider_name=provider, auth_flow="M2M")
    def _get_token(token: str = None):
        return token

    token = _get_token()
    client_factory = ClientFactory(ClientConfig(
        httpx_client=httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}",
                     "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id},
            timeout=120,
        ),
        streaming=True,
    ))

    remote_agent = A2AAgent(
        endpoint=endpoint,
        name="investigation_agent",
        description="Deep incident investigation specialist",
        a2a_client_factory=client_factory,
    )

    from strands import tool

    @tool
    def investigate_finding(finding_id: str, account_id: str, region: str = "us-east-1") -> str:
        """Perform a deep investigation of a GuardDuty finding.
        Correlates with CloudTrail, builds a timeline, assesses blast radius.
        Use this instead of calling GuardDuty/CloudTrail tools directly for thorough analysis."""
        return remote_agent(
            f"Investigate GuardDuty finding {finding_id} in account {account_id} region {region}."
        ).message

    return investigate_finding
```

Add `_make_investigation_tool(session_id)` to the tools list in `_create_agent()`.
Skip gracefully if `INVESTIGATION_AGENT_URL` is not set (so the main agent still works
during rollout before the investigation agent is deployed).

---

## Environment variables needed on orchestrator runtime

Add to `backend-stack.ts` runtime env vars:
```typescript
INVESTIGATION_AGENT_URL: investigationAgentStack.invocationsUrl,  // SSM param
INVESTIGATION_AGENT_PROVIDER: investigationAgentStack.credentialProviderName,
```

---

## Gateway tools: move vs. keep

For MVP — **keep** GuardDuty and CloudTrail tools on the Gateway (accessible to main agent).
The investigation agent also accesses them via Gateway MCP. No migration needed yet.

Moving them exclusively to the investigation agent is a future cleanup once the A2A
pattern is proven. Doing it now adds risk to the rollout.

---

## Deployment order

1. Deploy `InvestigationAgentStack` (CDK nested stack) — creates runtime, no orchestrator changes yet
2. Confirm agent card endpoint responds: `curl https://bedrock-agentcore.../runtimes/.../invocations/.well-known/agent.json`
3. Add `INVESTIGATION_AGENT_URL` + `INVESTIGATION_AGENT_PROVIDER` to orchestrator runtime env vars
4. Redeploy `BackendStack` with the new orchestrator code
5. Test end-to-end: ask the agent to "investigate finding X" and confirm it delegates

---

## Blog post update

Once this is working, update `blog/04-multi-agent-a2a.md`:
- Change Part 2 from forward-looking to past-tense ("we built" not "we will build")
- Update code snippets to match the actual `A2AAgent` API (not the Google ADK `RemoteA2aAgent` shown currently)
- Add a section on the actor/session propagation design decision
- Republish via `uv run python scripts/publish_post.py blog/04-multi-agent-a2a.md --tags aws,security,ai,agentcore,a2a`
