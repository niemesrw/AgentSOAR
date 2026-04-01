# AgentCore Identity — USER_FEDERATION OAuth2

This document captures hard-won knowledge about implementing the `USER_FEDERATION` OAuth2 flow from inside an AgentCore runtime container. The official docs cover M2M and the decorator pattern but leave significant gaps for the container-deployed case.

## What USER_FEDERATION does

When a user interacts with the agent and asks it to take actions on a third-party service (e.g. create a GitHub issue on their behalf), the agent needs an OAuth2 token scoped to *that user's* account on the third-party service. `USER_FEDERATION` is the AgentCore Identity flow that handles this:

1. Agent calls `get_resource_oauth2_token` with the user's workload identity token
2. If the user has previously authorized → returns the cached access token
3. If not → returns an authorization URL for the user to visit in their browser
4. After the user authorizes → GitHub redirects to the AgentCore callback URL, completing the session binding
5. Subsequent calls return the cached token

## Architecture

```
User browser
     │
     │  1. User asks agent to connect GitHub
     ▼
AgentCore Runtime (our container)
     │
     │  2. get_resource_oauth2_token(USER_FEDERATION, workload_token)
     ▼
AgentCore Identity (token vault)
     │
     │  3. Returns authorizationUrl (GitHub login)
     ▼
Runtime returns URL to user
     │
     │  4. User visits URL, logs into GitHub, clicks Authorize
     ▼
GitHub → redirects to AgentCore callback URL
     │
     │  5. AgentCore stores token in vault keyed to (workload_identity, user)
     ▼
Future calls return cached accessToken
```

## Required infrastructure

### 1. OAuth2 credential provider

Created via CDK custom resource (`infra-cdk/lambdas/github-oauth2-provider/`). Stores GitHub OAuth app client ID and secret in Secrets Manager. Returns a `callbackUrl` (e.g. `https://bedrock-agentcore.<region>.amazonaws.com/identities/oauth2/callback/<uuid>`).

The callback URL is unique per provider and account — it cannot be predicted. Store it in CDK:

```typescript
this.githubOAuthCallbackUrl = githubCredentialProvider.getAttString("CallbackUrl")
// in envVars:
GITHUB_OAUTH_CALLBACK_URL: this.githubOAuthCallbackUrl,
```

### 2. GitHub OAuth App

The callback URL from the credential provider must be registered as an authorized redirect URI in your GitHub OAuth App settings. This is a one-time manual step after first deploy — CDK cannot automate it.

### 3. Workload identity `allowedResourceOauth2ReturnUrls`

The runtime's workload identity (auto-created with the same name as the runtime ID, e.g. `AgentSOAR_FASTAgent-SgcWYdHjn4`) must have the credential provider callback URL in its allowed list. CDK doesn't manage this — do it after deploy:

```bash
aws bedrock-agentcore-control update-workload-identity \
  --name <runtime-id> \
  --allowed-resource-oauth2-return-urls "<callbackUrl from credential provider>" \
  --profile <profile> --region <region>
```

Tip: get the callback URL from:
```bash
aws bedrock-agentcore-control get-oauth2-credential-provider \
  --name <provider-name> --query callbackUrl --output text
```

### 4. IAM permissions on the agent role

```typescript
// data-plane: get workload token, check/fetch OAuth tokens
actions: [
  "bedrock-agentcore:GetWorkloadAccessToken",
  "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
  "bedrock-agentcore:GetOauth2CredentialProvider",
  "bedrock-agentcore:GetResourceOauth2Token",
  "bedrock-agentcore:CompleteResourceTokenAuth",
],
resources: [
  `arn:aws:bedrock-agentcore:${region}:${account}:oauth2-credential-provider/*`,
  `arn:aws:bedrock-agentcore:${region}:${account}:token-vault/*`,
  `arn:aws:bedrock-agentcore:${region}:${account}:workload-identity-directory/*`,
],

// Secrets Manager: token vault stores OAuth tokens as secrets
actions: ["secretsmanager:GetSecretValue"],
resources: [
  // Pattern: bedrock-agentcore-identity!default/oauth2/<provider-name>-<suffix>
  `arn:aws:secretsmanager:${region}:${account}:secret:bedrock-agentcore-identity!default/oauth2/${stackName}-github*`,
]
```

## The workload access token — critical finding

**Never call `GetWorkloadAccessToken` API directly from inside the runtime container.** It will fail with:

```
AccessDeniedException: Workload Identity does not belong to caller account
```

This error is misleading — the workload identity name and account are both correct. The real issue is that the API is not how you get a workload token from inside the container.

**The correct approach:** The AgentCore runtime framework injects the workload access token per-request via the `WorkloadAccessToken` HTTP header. The SDK's `app.py` stores it in `BedrockAgentCoreContext`. Read it with:

```python
from bedrock_agentcore.runtime import BedrockAgentCoreContext

workload_token = BedrockAgentCoreContext.get_workload_access_token()
```

This is what `@requires_access_token` uses internally via `_get_workload_access_token(client)`. If you use the decorator, this is handled for you.

## The OAuth2 callback URL — critical finding

`BedrockAgentCoreContext.get_oauth2_callback_url()` returns `None` in practice — the platform does not reliably inject the `OAuth2CallbackUrl` header. Without it, `get_resource_oauth2_token` throws:

```
ValidationException: You must provide a ResourceOauth2ReturnUrl to proceed with this flow
```

Always fall back to an env var:

```python
callback_url = BedrockAgentCoreContext.get_oauth2_callback_url() or os.environ.get("GITHUB_OAUTH_CALLBACK_URL")
if callback_url:
    req["resourceOauth2ReturnUrl"] = callback_url
```

## Complete github_connect implementation

```python
from bedrock_agentcore._utils.endpoints import get_data_plane_endpoint
from bedrock_agentcore.runtime import BedrockAgentCoreContext

@tool
def github_connect() -> str:
    """Connect your GitHub account to the SOAR agent."""
    provider_name = os.environ.get("GITHUB_CREDENTIAL_PROVIDER_NAME", "")
    if not provider_name:
        return "GITHUB_CREDENTIAL_PROVIDER_NAME is not configured."

    workload_token = BedrockAgentCoreContext.get_workload_access_token()
    if not workload_token:
        return "Workload access token not available. Must be called from within an AgentCore runtime."

    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    client = boto3.client(
        "bedrock-agentcore",
        region_name=region,
        endpoint_url=get_data_plane_endpoint(region),  # required — plain boto3 may use wrong endpoint
    )

    req = {
        "resourceCredentialProviderName": provider_name,
        "oauth2Flow": "USER_FEDERATION",
        "workloadIdentityToken": workload_token,
        "scopes": ["repo", "read:user"],
    }
    callback_url = BedrockAgentCoreContext.get_oauth2_callback_url() or os.environ.get("GITHUB_OAUTH_CALLBACK_URL")
    if callback_url:
        req["resourceOauth2ReturnUrl"] = callback_url

    token_resp = client.get_resource_oauth2_token(**req)

    if "accessToken" in token_resp:
        return "GitHub is already connected!"

    if "authorizationUrl" in token_resp:
        return f"Authorize GitHub at: {token_resp['authorizationUrl']}"
```

## Errors we hit and what they actually meant

| Error | Apparent cause | Actual cause |
|---|---|---|
| `AccessDeniedException: Workload Identity does not belong to caller account` | Wrong account or wrong workload name | Called `GetWorkloadAccessToken` API directly from inside the container — don't do this |
| `ValidationException: You must provide a ResourceOauth2ReturnUrl` | Missing callback URL config | `BedrockAgentCoreContext.get_oauth2_callback_url()` returned None; need env var fallback |
| `AccessDeniedException: secretsmanager:GetSecretValue` | IAM permissions | Agent role missing access to `bedrock-agentcore-identity!default/oauth2/<stack>-github*` |
| `KeyError: credentialProviderArn` | API response shape | AgentCore API sometimes omits this field; build the ARN from account/region/name as fallback |
| Lambda target only supports `GATEWAY_IAM_ROLE` | Wrong credential type | `CfnGatewayTarget` with Lambda target only accepts `GATEWAY_IAM_ROLE`, not `OAUTH` |

## What we still don't know

- Whether `BedrockAgentCoreContext.get_oauth2_callback_url()` is *ever* populated in production (or if env var is always needed)
- Whether the `@requires_access_token` decorator with `USER_FEDERATION` works end-to-end from a deployed container (we hand-rolled the flow instead)
- The exact session binding mechanism after the user authorizes (AgentCore handles the redirect, but the exact polling/completion flow is undocumented for manual implementations)
