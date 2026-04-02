---
name: AgentSOAR OAuth architecture
description: How per-user 3LO OAuth works in AgentSOAR — replaced AgentCore Identity with our own implementation
type: project
---

AgentCore Identity was abandoned for GitHub OAuth. Replaced with a custom multi-provider OAuth layer.

**Why:** AgentCore Identity's 3LO (USER_FEDERATION) was too painful and brittle. The workload token dance, Custom Resource lifecycle management, and callback URL registration were all impedance mismatches with what we actually needed.

**New architecture:**

Token storage: SSM SecureString at `/{stack}/oauth-token/{provider}/{user_id}` — fully per-user, per-provider.

Pending state:
- Device flow: `/{stack}/oauth-device/{provider}/{user_id}`  
- Web flow (PKCE): `/{stack}/oauth-pending/{nonce}`

Provider credentials: Secrets Manager at `/{stack}/oauth-creds/{provider}` — `{clientId, clientSecret}`

**Code locations:**
- `patterns/agui-strands-agent/tools/oauth/` — generic OAuth2 module (store, device_flow, web_flow, providers)
- `patterns/agui-strands-agent/tools/github/strands_tools.py` — GitHub tools as native Strands @tool functions via `make_github_tools(user_id)`
- `infra-cdk/lambdas/oauth-callback/index.py` — generic web-flow callback Lambda (handles all providers)

**Adding a new provider (Slack, Gmail, etc.):**
1. Add config to `tools/oauth/providers.py`
2. Create `tools/<provider>/strands_tools.py` with `make_<provider>_tools(user_id)`
3. Add token endpoint to `infra-cdk/lambdas/oauth-callback/index.py` `_TOKEN_URLS`
4. Run `scripts/configure-oauth.py --provider <name>` to store credentials
5. Wire tools into `agent.py` `_create_agent()`

**GitHub uses device flow** (no callback URL to register — user enters code at github.com/login/device).
**Slack/Gmail use web flow** with PKCE — callback URL is the `OAuthCallbackUrl` CDK output.

**How to apply:** When touching OAuth or adding new providers, follow the pattern above. Never go back to AgentCore Identity for 3LO.
