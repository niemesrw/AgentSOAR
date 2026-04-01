# Building a Modern Agentic SOAR: From Template to Self-Improving Security Platform

> **Status: In progress** — this post documents the journey as we build it.

---

## Why SOAR is Broken

Security Orchestration, Automation, and Response platforms promised to fix alert fatigue. They didn't. The core problem: traditional SOAR runs **rigid playbooks**. An alert arrives, it pattern-matches against a known rule, and a predetermined sequence of steps executes. When the alert doesn't fit a known pattern — which is increasingly common with sophisticated threats — the playbook stalls and a human has to pick it up cold.

The result is the worst of both worlds: analysts spend their time babysitting automation instead of doing real investigation, and novel threats slip through the cracks between playbooks.

What we actually want is an analyst that can reason: *"This alert looks like lateral movement. Let me pull the host's EDR telemetry, correlate with identity logs, check threat intel, and then recommend containment."* And crucially — one that gets better each time it encounters a gap.

---

## The Agentic Alternative

Amazon Bedrock AgentCore is a managed runtime for deploying AI agents at production scale. It handles the undifferentiated heavy lifting — auth, session management, memory, secure tool access — so you can focus on agent behavior.

Paired with the [FAST template](https://github.com/awslabs/fullstack-solution-template-for-agentcore) (Fullstack AgentCore Solution Template), you get a production-ready fullstack starting point: a React frontend with real-time streaming via the [AG-UI protocol](https://docs.ag-ui.com/concepts/overview), a containerized agent backend, and CDK infrastructure — all wired together.

We forked FAST as [AgentSOAR](https://github.com/niemesrw/AgentSOAR) and are building a modern agentic SOAR on top of it. This post documents what we discover along the way.

### What the stack enables

| SOAR capability | How AgentSOAR delivers it |
|---|---|
| Real-time visibility into agent actions | AG-UI streaming — every tool call visible live in the UI |
| Security tool integrations | AgentCore Gateway MCP — each integration is an MCP server |
| Persistent incident context | AgentCore Memory across sessions |
| Log/artifact analysis | Code Interpreter |
| Human-in-the-loop approvals | CopilotKit action primitives |
| Multi-step reasoning over alerts | Strands or LangGraph agent patterns |
| Auth + session isolation per analyst | AgentCore Runtime handles it |

---

## Step 1: GitHub as the Agent's Nervous System

The first integration we're building is GitHub — and it's not just because GitHub has a great MCP server (though it does). It's because GitHub becomes the connective tissue of the entire platform:

- **Incidents → Issues**: every alert the agent investigates becomes a tracked GitHub issue with full context, timeline, and resolution notes
- **Gaps → Issues**: when the agent hits a missing tool or playbook, it files an issue against itself
- **Remediation → PRs**: containment scripts, config changes, and rule updates flow through pull requests with proper review
- **Audit trail in git**: the entire history of what the agent did, why, and what changed lives in version control

This is a meaningful shift from traditional SOAR case management. Instead of a proprietary incident database, you get a transparent, reviewable, forkable record of every decision.

### How it works technically

AgentCore Gateway supports MCP servers as tool providers. We configure the [GitHub MCP server](https://github.com/github/github-mcp-server) in the Gateway, store credentials in Secrets Manager, and the agent gets GitHub tools natively: create issue, open PR, search code, add comment, assign label.

#### What we built

Three files, one CDK update:

- **`gateway/tools/github/github_lambda.py`** — Lambda that wraps the GitHub REST API using stdlib `urllib` (no external dependencies). Reads a GitHub PAT from Secrets Manager (cached per container). Handles six tools: `github_create_issue`, `github_update_issue`, `github_add_comment`, `github_list_issues`, `github_create_pr`, `github_search_issues`.
- **`gateway/tools/github/tool_spec.json`** — JSON schema definitions for each tool, with descriptions tuned for a SOAR context.
- **`infra-cdk/lib/backend-stack.ts`** — A new `CfnGatewayTarget` pointing at the GitHub Lambda, a Secrets Manager secret for the PAT, and the necessary IAM grants.

After `cdk deploy`, populate the PAT:

```bash
# Get the secret ARN from the stack output GitHubPatSecretArn, then:
aws secretsmanager put-secret-value \
  --secret-id <GitHubPatSecretArn> \
  --secret-string "ghp_your_token_here"
```

The agent now has GitHub as a native tool — no additional configuration in the agent code needed. The Gateway discovers the tools automatically via MCP.

---

## Step 2: Closing the Self-Improvement Loop

GitHub integration alone makes the agent a capable responder. Log ingestion makes it a learning system.

The idea: capture structured logs from every agent run — what alert came in, what tools were called, what reasoning was applied, where it got stuck. Feed those logs back into a periodic review cycle where the agent (or a separate evaluator) identifies recurring gaps and files GitHub issues describing them.

The loop looks like this:

```
Alert arrives
  → agent investigates using available tools
  → hits a gap (missing tool, incomplete playbook, ambiguous data)
  → logs the gap with context
  → periodic job summarizes gaps → files GitHub issues
  → issues get prioritized, addressed, merged
  → agent is measurably better next iteration
```

This is the narrative no traditional SOAR can tell. The platform improves itself, with humans in the loop for review and approval, and a full audit trail of every improvement.

*[Log ingestion architecture — coming soon]*

---

## Step 0: Actually Deploying the Thing

Before any SOAR logic, you need a running stack. Here's exactly what it took.

### Prerequisites

| Tool | Notes |
|------|-------|
| Node.js 20+ | |
| AWS CDK CLI | `npm install -g aws-cdk` |
| Python 3.11+ | |
| [OrbStack](https://orbstack.dev/) | Lightweight Docker Desktop replacement for Mac. Docker Desktop also works. CDK needs a container runtime to build Lambda layers and the agent image. |
| AWS CLI | Configured with a named profile |
| [AgentCore MCP Server](https://github.com/awslabs/amazon-bedrock-agentcore-mcp-server) | Optional but recommended — gives your AI coding assistant live access to AgentCore docs. See config below. |

**AgentCore MCP Server** (add to your Claude Code / Cursor MCP config):

```json
{
  "mcpServers": {
    "bedrock-agentcore-mcp-server": {
      "command": "uvx",
      "args": ["awslabs.amazon-bedrock-agentcore-mcp-server@latest"],
      "env": { "FASTMCP_LOG_LEVEL": "ERROR" },
      "disabled": false,
      "autoApprove": ["search_agentcore_docs", "fetch_agentcore_doc"]
    }
  }
}
```

This lets Claude Code search and fetch AgentCore documentation inline while you're building — no context-switching to browser tabs.

One non-obvious thing: **macOS native containers won't work here**. AgentCore Runtime requires a Linux ARM64 image. OrbStack runs Linux containers natively on Apple Silicon — no Rosetta, no cross-compilation needed.

### The deploy sequence

```bash
# 1. Clone your fork
gh repo clone niemesrw/AgentSOAR
cd AgentSOAR

# 2. Set your stack name in infra-cdk/config.yaml
#    (leave admin_user_email as null — set it locally, don't commit it)

# 3. Install CDK deps
cd infra-cdk && npm install

# 4. Bootstrap (once per account/region)
cdk bootstrap --profile your-profile

# 5. Deploy the backend
cdk deploy --profile your-profile

# 6. Deploy the frontend
cd ..
AWS_PROFILE=your-profile python3 scripts/deploy-frontend.py
```

Total time: about 6 minutes. CDK creates Cognito, ECR, the AgentCore Runtime, AgentCore Gateway, a DynamoDB feedback table, CloudFront, and Amplify Hosting in one shot. The frontend script pulls the stack outputs, generates `aws-exports.json`, builds the React app, and pushes it to Amplify.

### The one gotcha

OrbStack puts its binaries in `~/.orbstack/bin/` but that path may not be in your shell's PATH when CDK runs (especially in a terminal session that predated OrbStack starting). If you hit `spawnSync docker ENOENT`, prefix the command:

```bash
PATH="$PATH:$HOME/.orbstack/bin" cdk deploy --profile your-profile
```

And add it permanently:
```bash
echo 'export PATH="$PATH:$HOME/.orbstack/bin"' >> ~/.zshrc
```

### First login

Since `admin_user_email` is `null` in the committed config, no Cognito user is auto-created. Create one manually in the [Cognito console](https://console.aws.amazon.com/cognito/) — find your user pool, create a user, mark email as verified. You'll get a temporary password to change on first login.

### Choosing the right model and agent pattern

The default FAST config ships with `strands-single-agent` and `us.anthropic.claude-sonnet-4-5-20250929-v1:0`. Neither was right for us.

**Model**: The `us.*` cross-region inference prefix caused an `AccessDeniedException` on `ConverseStream` immediately after deploy. Checking `aws bedrock list-inference-profiles` showed both `us.anthropic.claude-sonnet-4-6` and `global.anthropic.claude-sonnet-4-6` as ACTIVE. We switched to `global.anthropic.claude-sonnet-4-6` — the latest model, widest availability, no access issues.

**Agent pattern**: FAST ships multiple patterns. The `agui-strands-agent` pattern produces native AG-UI SSE events, which the frontend parses with the AG-UI client for real-time streaming of every tool call and reasoning step. The `strands-single-agent` pattern uses a simpler response format with a different frontend parser. For a SOAR dashboard where analysts watch the agent work in real time, AG-UI is the right choice — you want to see every tool invocation as it happens, not a final answer.

Switching is a one-line change in `infra-cdk/config.yaml`:

```yaml
pattern: agui-strands-agent
```

CDK re-builds and pushes a new container image on the next deploy. The Runtime update took about 8 seconds.

### Wiring up the GitHub PAT

After deploy, the GitHub Gateway tool exists but has a CDK-generated random placeholder in Secrets Manager — it won't authenticate until you replace it with a real token.

1. Create a GitHub PAT at https://github.com/settings/tokens/new
   - Note: `AgentSOAR-gateway`
   - Scopes: **`repo`** (covers issues, PRs, comments)

2. Store it:

```bash
aws secretsmanager put-secret-value \
  --secret-id /AgentSOAR/github-pat \
  --secret-string "ghp_your_token_here" \
  --profile your-profile
```

No redeploy needed — the Lambda fetches the secret on cold start and caches it per container. The next invocation picks it up automatically.

### It works

![AgentSOAR running after first deploy](images/agentsoar-first-deploy.png)

The baseline FAST chat UI — logged in, agent running on Claude Sonnet 4.6 via the AG-UI pattern. From here, we start turning it into a SOAR.

---

## What We've Built So Far

- [x] Forked FAST as AgentSOAR
- [x] Deployed to AWS (CDK + Amplify)
- [x] GitHub MCP server configured in AgentCore Gateway
- [ ] First incident scenario end-to-end
- [ ] Log ingestion pipeline
- [ ] Self-improvement loop demo

---

## What Surprised Us

- **OrbStack PATH issue**: CDK spawns Docker as a subprocess and inherits the shell PATH at launch time. If Docker wasn't in PATH when you opened the terminal, CDK can't find it even after OrbStack starts. Prefix with `PATH=...` or restart the terminal.
- **No personal data in config**: Committing `admin_user_email` to a public repo is a bad idea — Copilot caught it in code review. Keep it local.
- **`cdk deploy` does more than you think**: AgentCore Runtime, Gateway, Memory, OAuth2 credential provider Lambda, Cognito, CloudFront, Amplify — all in one command. The FAST template earns its name.
- **Default model and pattern aren't production-ready**: The shipped defaults (`strands-single-agent`, `us.anthropic.claude-sonnet-4-5`) caused an immediate `AccessDeniedException`. Check `list-inference-profiles` for what's actually ACTIVE in your account, use the `global.*` prefix for best availability, and pick the agent pattern that matches your frontend parser before first deploy.

---

## What's Next

- Multi-agent triage: specialized sub-agents for enrichment, containment, and reporting working in parallel
- CopilotKit integration for richer human-in-the-loop approval flows
- Alert ingestion webhooks (PagerDuty, Splunk, custom)
- Playbook-as-code: encoding SOC runbooks as structured LangGraph graphs

---

## Resources

- [AgentSOAR on GitHub](https://github.com/niemesrw/AgentSOAR)
- [FAST template](https://github.com/awslabs/fullstack-solution-template-for-agentcore)
- [Amazon Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/)
- [AG-UI Protocol](https://docs.ag-ui.com/concepts/overview)
- [GitHub MCP Server](https://github.com/github/github-mcp-server)
- [CopilotKit](https://www.copilotkit.ai/)
