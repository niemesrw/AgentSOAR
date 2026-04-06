# Part 4 — Multi-Agent Incident Response with A2A

Two things have become clear after running AgentSOAR against real GuardDuty findings:
a single agent with a flat list of tools works for demos but starts showing seams in
production, and the agent forgets everything it learned the moment the session ends.
This post covers both fixes: a memory hooks overhaul that gives the agent persistent
institutional knowledge, and an A2A multi-agent architecture that scales the
orchestration model to match how real incident response actually works.

## The limits of a single agent

The current setup — one Strands agent, all tools in scope, session memory — handles
basic queries well. Ask it about active GuardDuty findings and it does the right thing.
But real incident response is not a single query; it's a pipeline:

```
Alert arrives
  → Is this real or noise? (triage)
  → What happened? (investigation — multiple data sources)
  → What do we do about it? (playbook lookup or generation)
  → Who needs to know? (notification)
  → What changes? (remediation)
```

Each of these steps has different tool access requirements, different reasoning goals,
and different acceptable latencies. Collapsing them into a single agent with a
sprawling system prompt means you're writing one prompt that is simultaneously a triage
specialist, a forensic analyst, a runbook executor, and a communication coordinator.
That prompt becomes impossible to reason about and impossible to test.

The other issue is memory. AgentCore Memory is wired up for session persistence, but
the agent has no mechanism to learn from past incidents. Every new session starts cold.
If the agent triaged a `Recon:IAMUser/MaliciousIPCaller` finding last week and learned
that the actor IP belongs to a known pen test vendor — that context is gone.

## What the AWS A2A sample taught us

AWS released an [A2A multi-agent incident response sample](https://github.com/awslabs/agentcore-samples/tree/main/02-use-cases/A2A-multi-agent-incident-response)
in the [agentcore-samples](https://github.com/awslabs/agentcore-samples) repository
that solves both problems. Three agents — a monitoring agent
(Strands/Claude), a web search agent (OpenAI), and an orchestrating host agent (Google
ADK) — run on separate AgentCore Runtimes and communicate via the
[A2A protocol](https://google.github.io/A2A/), a JSON-RPC 2.0 standard for
agent-to-agent communication.

The memory hooks pattern in the monitoring agent is particularly well-designed. Rather
than a flat "load last N messages", it uses AgentCore Memory's namespace and strategy
system to maintain two distinct knowledge stores and injects them at the right points in
the agent's lifecycle.

Two things from the sample are directly applicable to AgentSOAR. The third — the
specific agent frameworks (Google ADK, OpenAI) — is not, since AgentSOAR is
Strands-native. We're taking the patterns, not the code.

## Part 1 — Memory hooks

### The problem with session-only memory

With session-only memory, the agent's knowledge looks like this:

```
Session 1: "IAMUser/MaliciousIPCaller from 1.2.3.4 — investigated, confirmed pen test"
[session ends]
Session 2: "IAMUser/MaliciousIPCaller from 1.2.3.4 — ?"
```

The second analyst (or the same analyst the next day) starts from scratch. The agent
has no way to say "we've seen this before."

### How it works

![AgentSOAR memory architecture — store and retrieve paths](images/memory-architecture.svg)

The diagram shows the two paths. **Store** (top): every conversation turn is saved via
`create_event()` to the event log. Haiku then runs extraction asynchronously —
minutes later — to distill facts into the long-term vector namespaces.
**Retrieve** (bottom): on each new user message, `retrieve_memories()` does a
cosine similarity search across both namespaces and injects the results as
`<soar-memory-context>` into the prompt before the model ever sees the question.

The storage is fully managed by AWS — the vector index is opaque, similar to a
serverless Pinecone. You interact with it only via the `bedrock-agentcore` API.

### Two memory namespaces

The fix is two namespaces with different extraction strategies in AgentCore Memory:

| Namespace | Strategy | What lives here |
|---|---|---|
| `/technical-issues/{user_id}` | `CustomMemoryStrategy` | Recurring patterns, known-bad IPs, false positive signatures, account-specific quirks |
| `/knowledge/{user_id}` | `SemanticMemoryStrategy` | Factual knowledge about the environment — what services run where, blast radius of specific resources, IAM role usage patterns |

`CustomMemoryStrategy` uses an extraction prompt to decide what to retain — it doesn't
blindly store every message, it distills. `SemanticMemoryStrategy` stores embeddings and
retrieves by similarity, so the agent can retrieve relevant past context even when
the exact terms don't match.

### How the retrieval hooks work

The `bedrock-agentcore` SDK's `AgentCoreMemorySessionManager` already registers a
`MessageAddedEvent` hook internally. Before each user message reaches the model, the
hook calls `retrieve_memories()` against every namespace listed in `retrieval_config`
and injects the results as `<soar-memory-context>` in the prompt. We don't need to
write the hook — we just need to configure which namespaces to search and how.

Two configuration parameters per namespace control retrieval quality:

- **`top_k`** — how many records to return from semantic search
- **`relevance_score`** — minimum cosine similarity to include a record (0.0–1.0)

Technical issues gets a tighter threshold (`0.3`) because incident facts should be
highly specific. The knowledge namespace is broader (`0.2`) since environment facts
are useful even at lower similarity.

### The actual change

This is the complete change to `patterns/agui-strands-agent/agent.py`:

```python
from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
    RetrievalConfig,          # added
)

def _create_session_manager(user_id: str, session_id: str) -> AgentCoreMemorySessionManager:
    memory_id = os.environ.get("MEMORY_ID")
    if not memory_id:
        raise ValueError("MEMORY_ID environment variable is required")
    config = AgentCoreMemoryConfig(
        memory_id=memory_id,
        session_id=session_id,
        actor_id=user_id,
        # Search long-term namespaces before each user message.         (added)
        # The session manager's MessageAddedEvent hook retrieves         (added)
        # relevant records and injects them as <soar-memory-context>.   (added)
        retrieval_config={                                               # added
            "/technical-issues/{actorId}": RetrievalConfig(             # added
                top_k=3, relevance_score=0.3                            # added
            ),                                                           # added
            "/knowledge/{actorId}": RetrievalConfig(                    # added
                top_k=5, relevance_score=0.2                            # added
            ),                                                           # added
        },                                                               # added
        context_tag="soar-memory-context",                              # added
    )
    return AgentCoreMemorySessionManager(
        agentcore_memory_config=config,
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )
```

The session manager handles persistence automatically: every exchange is saved to
short-term memory via `create_event()` after invocation, and the memory strategies
run extraction in the background to promote relevant facts into the long-term namespaces.

## Part 2 — A2A multi-agent architecture

### The target architecture

The single agent becomes an orchestrating host agent. The Investigation Agent is the
first specialized sub-agent, running on its own AgentCore Runtime and communicating
over A2A:

```
Incident (GuardDuty finding, CloudTrail anomaly, manual query)
        │
        ▼
Orchestrator Agent  ─── agui-strands-agent (gains investigate_finding tool)
        │
        └──► Investigation Agent   ─── GuardDuty + CloudTrail deep analysis
                                       (separate AgentCore Runtime, A2A/HTTP)
```

Playbook and Notification agents follow the same pattern — deploy each as a new
`AgentCore Runtime` with `HTTP` protocol, wire it into the orchestrator via `A2AAgent`.

### Why not just add more tools to the single agent?

Tools don't isolate failure or permissions. The investigation agent needs read access to
GuardDuty and CloudTrail across accounts — scoping that to a separate IAM execution role
on a separate runtime is significantly cleaner than adding it to the orchestrator's role
alongside Slack write access and GitHub write access.

Sub-agents are also independently deployable. The investigation agent can be updated
with a new tool without redeploying the orchestrator or touching the frontend.

Finally, sub-agents compose naturally. Once the playbook agent exists, the orchestrator
can delegate investigation and playbook lookup concurrently — standard A2A parallel
delegation.

### The A2A server

Strands 1.x includes `A2AServer` in `strands.multiagent.a2a` — native A2A support
without needing to implement `AgentExecutor` or `TaskUpdater` manually. The
investigation agent's server is three lines:

```python
# patterns/investigation-agent/main.py

from strands.multiagent.a2a import A2AServer

agent = create_agent(session_id="default", actor_id="default")

server = A2AServer(
    agent=agent,
    http_url=RUNTIME_INVOCATIONS_URL,   # constructed from SSM at startup
    enable_a2a_compliant_streaming=True,
)

app = server.to_starlette_app()   # passed to uvicorn
```

`A2AServer` wraps the Strands agent, generates the Agent Card at
`/.well-known/agent.json`, and handles the A2A JSON-RPC 2.0 task lifecycle. We don't
need to implement `AgentExecutor` — Strands handles that via `StrandsA2AExecutor`
internally.

**One circular dependency to solve.** The runtime's invocations URL contains the
runtime ARN, which doesn't exist until after the runtime is deployed. We can't set it
as a CDK environment variable. Instead, CDK stores the ARN in SSM, and `main.py`
constructs the URL from SSM at startup:

```python
runtime_arn = get_ssm_parameter(f"/{stack_name}/investigation-agent-runtime-arn")
RUNTIME_INVOCATIONS_URL = (
    f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{runtime_arn}/invocations"
)
```

### How the orchestrator delegates

The orchestrator gains an `investigate_finding` Strands tool backed by `A2AAgent` from
`strands.agent.a2a_agent`. The tool is created per-session with a fresh M2M token and
the session ID for context propagation:

```python
# patterns/agui-strands-agent/agent.py  (additions)

from a2a.client import ClientConfig, ClientFactory
from strands import tool
from strands.agent.a2a_agent import A2AAgent
from bedrock_agentcore.identity.auth import requires_access_token

def _make_investigation_tool(session_id: str):
    investigation_agent_url = get_ssm_parameter(f"/{stack_name}/investigation-agent-url")

    @requires_access_token(provider_name=provider, auth_flow="M2M")
    def _get_token(token: str = None):
        return token

    token = _get_token()
    client_factory = ClientFactory(ClientConfig(
        httpx_client=httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {token}",
                "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
            },
            timeout=120,
        ),
        streaming=True,
    ))

    remote_agent = A2AAgent(
        endpoint=investigation_agent_url,
        name="investigation_agent",
        description="Deep incident investigation specialist",
        a2a_client_factory=client_factory,
    )

    @tool
    def investigate_finding(finding_id: str, account_id: str, region: str = "us-east-1") -> str:
        """Perform a deep investigation of a GuardDuty finding.
        Correlates with CloudTrail events, builds a timeline, and assesses blast radius.
        Use this for thorough analysis instead of calling GuardDuty/CloudTrail tools directly."""
        return remote_agent(
            f"Investigate GuardDuty finding {finding_id} in account {account_id} region {region}."
        ).message

    return investigate_finding
```

The tool is added to the orchestrator's tool list alongside the gateway client. The
function returns `None` gracefully if `investigation-agent-url` isn't in SSM yet —
so the orchestrator continues to work during rollout before the investigation agent
is deployed.

The M2M token uses the same credential provider as the Gateway (`GATEWAY_CREDENTIAL_PROVIDER_NAME`).
No new Cognito scopes are needed — the existing machine client can call both the Gateway
and the investigation agent's runtime, since both are secured by the same Cognito user pool.

### CDK changes

`InvestigationAgentStack` is a new CDK nested stack added to `FastMainStack`. It uses
the same `AgentCoreRole` construct and the L2 `agentcore.Runtime` with `HTTP` protocol:

```typescript
// infra-cdk/lib/investigation-agent-stack.ts

const runtime = new agentcore.Runtime(this, "InvestigationAgentRuntime", {
  runtimeName: `${props.stackName}_investigation_agent`,
  agentRuntimeArtifact: agentcore.AgentRuntimeArtifact.fromAsset(repoRoot, {
    platform: ecr_assets.Platform.LINUX_ARM64,
    file: "patterns/investigation-agent/Dockerfile",
  }),
  executionRole: agentRole,
  networkConfiguration: agentcore.RuntimeNetworkConfiguration.usingPublicNetwork(),
  protocolConfiguration: agentcore.ProtocolType.HTTP,   // not AGUI
  authorizerConfiguration: agentcore.RuntimeAuthorizerConfiguration.usingJWT(
    cognitoDiscoveryUrl,
    [props.machineClientId],  // accept tokens issued by orchestrator's machine client
  ),
  environmentVariables: {
    STACK_NAME: props.stackName,
    MEMORY_ID: props.memoryId,
    GATEWAY_CREDENTIAL_PROVIDER_NAME: `${props.stackName}-runtime-gateway-auth`,
  },
})

// Runtime ARN → SSM (for main.py URL construction at startup)
new ssm.StringParameter(this, "InvestigationAgentRuntimeArnParam", {
  parameterName: `/${props.stackName}/investigation-agent-runtime-arn`,
  stringValue: runtime.agentRuntimeArn,
})

// Invocations URL → SSM (for orchestrator to discover)
new ssm.StringParameter(this, "InvestigationAgentUrlParam", {
  parameterName: `/${props.stackName}/investigation-agent-url`,
  stringValue: `https://bedrock-agentcore.${region}.amazonaws.com/runtimes/${runtime.agentRuntimeArn}/invocations`,
})
```

The investigation agent shares the existing memory resource — same `memoryId`, same
two namespaces. Past incidents investigated by the investigation agent become retrievable
context for future sessions, regardless of which agent made the call.

### Inter-agent context passing

The orchestrator passes the session ID in the `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`
header on every A2A call. In the current implementation the investigation agent uses
`session_id="default"` and `actor_id="default"` for all requests — a known MVP
limitation. The memory namespaces (`/technical-issues/{actorId}` and
`/knowledge/{actorId}`) are therefore shared across all callers at this stage.

The investigation agent's memory hooks are wired with the same retrieval config as the
orchestrator, so past investigation reports do surface as `<soar-memory-context>` —
known-good baselines and confirmed false positives carry forward. Actor-scoped isolation
(each analyst getting their own memory slice) requires subclassing `StrandsA2AExecutor`
to extract `X-Amzn-Bedrock-AgentCore-Runtime-Custom-Actorid` from the request context
and pass it to `create_agent()`. That's tracked in the "What's next" section below.

## Design decisions

**Keep the existing `agui-strands-agent` as the orchestrator.** It already owns the
AG-UI streaming connection and the Cognito user session. Making it the entry point
means no changes to the frontend or to how sessions are established.

**`A2AAgent` as a `@tool`, not a sub-agent.** Strands supports both patterns. Using
`@tool` keeps the orchestrator's tool list uniform (gateway client, code interpreter,
GitHub tools, investigation tool) and lets the agent use investigation as one step in a
larger reasoning chain rather than a full delegation. The direct GuardDuty and CloudTrail
tools remain available for quick lookups.

**Sub-agents are Strands, not mixed frameworks.** The A2A sample uses Google ADK,
Strands, and OpenAI — heterogeneous by design to demonstrate protocol interop. For
AgentSOAR, Strands uniformity is more valuable than framework variety. All agents use
the same SDK, same tool pattern, same deployment model.

**GuardDuty and CloudTrail tools stay on the Gateway for now.** The investigation agent
accesses them via the same Gateway MCP client as the orchestrator. Moving them
exclusively to the investigation agent is a future cleanup once the A2A pattern is
proven — doing it now adds migration risk with no benefit.

**A2A is additive.** The orchestrator with no investigation agent URL in SSM behaves
identically to the previous single-agent setup. The investigation agent can be deployed
and validated independently before wiring it into the orchestrator.

## What's next

- **Playbook agent** — load account-specific runbooks from SSM and generate
  step-by-step containment instructions tailored to the affected resource type.
  Same CDK pattern as `InvestigationAgentStack`.
- **Notification agent** — the GitHub and Slack tools (already OAuth-connected) move
  here. The orchestrator delegates "file a GitHub issue for this incident" rather than
  calling GitHub tools directly.
- **Per-request session isolation** — subclass `StrandsA2AExecutor` to extract
  `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` and
  `X-Amzn-Bedrock-AgentCore-Runtime-Custom-Actorid` from the A2A `RequestContext` and
  pass them to `create_agent()` instead of using `"default"` for both.
- **Parallel delegation** — once all three sub-agents exist, update the orchestrator
  prompt to run investigation and playbook lookup concurrently via A2A parallel
  streaming.
