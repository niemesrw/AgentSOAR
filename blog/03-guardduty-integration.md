# Part 3 — GuardDuty Threat Detection & Automated Triage

AgentSOAR can now see your AWS threat landscape. This post covers the GuardDuty
integration: four agent-callable tools, an EventBridge ingestion pipeline, and the
design decisions along the way.

## What was added

### Four gateway tools (`gateway/tools/guardduty_tool/`)

| Tool | What it does |
|------|-------------|
| `get_guardduty_findings` | List active (non-archived) findings filtered by severity, region, and account |
| `describe_guardduty_finding` | Human-readable explanation: type, severity, affected resource, actor geo |
| `archive_guardduty_finding` | Suppress confirmed false positives or remediated findings |
| `triage_guardduty_finding` | 4-step automated playbook: classify → enrich → risk score → remediation steps |

All four are backed by a single Lambda (`guardduty_lambda.py`) that the AgentCore
Gateway routes to via a `CfnGatewayTarget`.

### EventBridge ingestion

An EventBridge rule on `aws.guardduty` / `GuardDuty Finding` feeds new findings
directly into the Lambda in near-real time. The handler detects EventBridge
invocations by checking `event["source"] == "aws.guardduty"` and routes them to a
separate `_handle_eventbridge()` path — EventBridge does not set `client_context`,
so it must be dispatched before the Gateway tool path is attempted.

## Design decisions

**Single Lambda, multiple tools.** GuardDuty's APIs are cohesive. Splitting them
across four Lambdas would multiply cold-start overhead and IAM complexity for no
gain.

**Only active findings.** `get_guardduty_findings` filters `service.archived = false`
so the agent never acts on already-suppressed findings.

**ArchiveFindings on a separate IAM statement.** The read actions (`ListDetectors`,
`ListFindings`, `GetFindings`) and the write action (`ArchiveFindings`) are in
separate IAM policy statements scoped to `arn:aws:guardduty:*:<account>:detector/*`.
This makes it easy to remove write access independently if needed, and avoids the
overly-broad `resources: ["*"]` pattern.

**Tool description warns before archiving.** The `archive_guardduty_finding` tool
description explicitly marks the action as destructive so the agent's reasoning loop
treats it with appropriate caution.

## Triage output example

```
# Automated Triage Report — SSH brute force attack detected
Finding ID: abc123  |  Account: 123456789012  |  Region: us-east-1

## Step 1 — Classification
Finding type : UnauthorizedAccess:EC2/SSHBruteForce
Severity     : MEDIUM (5.0)

## Step 2 — Resource Enrichment
Resource type: Instance
  Instance ID : i-0abc123 (t3.micro, running)
Actor IP : 1.2.3.4 (Amsterdam, Netherlands / Example ISP)
Occurrences  : 3

## Step 3 — Risk Assessment
Adjusted risk score : 5.5 / 10
Risk factors        : repeated occurrences (>5)

## Step 4 — Recommended Actions
  1. Review the affected resource configuration and recent CloudTrail events.
  2. Verify the activity is authorized; escalate if it cannot be confirmed.
  3. Apply least-privilege principles to the implicated IAM entity.
```

## What's next

- Wire `_handle_eventbridge` to push new findings into a DynamoDB findings store so
  the agent can answer "what came in while I was away?"
- Add a `list_guardduty_findings_since` tool backed by the store for time-bounded
  queries.
- Extend triage playbooks with account-specific runbooks loaded from SSM.
