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

## What We've Built So Far

*[This section will be updated as the build progresses]*

- [x] Forked FAST as AgentSOAR
- [x] GitHub MCP server configured in AgentCore Gateway
- [ ] First incident scenario end-to-end
- [ ] Log ingestion pipeline
- [ ] Self-improvement loop demo

---

## What Surprised Us

*[Honest lessons — to be filled in as we build]*

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
