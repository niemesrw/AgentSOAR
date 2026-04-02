"""Generic OAuth2 web-flow callback handler.

Handles the redirect from any OAuth provider after user authorization.
Exchanges the authorization code (+ PKCE verifier) for tokens and stores
them in SSM SecureString.

URL: GET /oauth/callback?code=<auth_code>&state=<nonce>[&error=<reason>]

The nonce (state param) maps to a pending SSM entry written by the agent
when it started the web flow. That entry contains:
    provider, user_id, code_verifier, redirect_uri

Provider credentials (clientId + clientSecret) are read from Secrets Manager
at /{STACK_NAME}/oauth-creds/{provider}.
"""

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_REGION = os.environ.get("AWS_REGION", "us-east-1")
_STACK = os.environ.get("STACK_NAME", "AgentSOAR")
_ssm = boto3.client("ssm", region_name=_REGION)
_sm = boto3.client("secretsmanager", region_name=_REGION)

# Token endpoints per provider — must match providers.py in the agent
_TOKEN_URLS: dict[str, str] = {
    "github": "https://github.com/login/oauth/access_token",
    "slack": "https://slack.com/api/oauth.v2.access",
    "gmail": "https://oauth2.googleapis.com/token",
}


def _pop_pending(nonce: str) -> dict | None:
    """Fetch and delete pending web-flow state. Returns None if missing or expired."""
    param_name = f"/{_STACK}/oauth-pending/{nonce}"
    try:
        resp = _ssm.get_parameter(Name=param_name)
        _ssm.delete_parameter(Name=param_name)
        data = json.loads(resp["Parameter"]["Value"])
        if time.time() > data.get("_expires_at", float("inf")):
            logger.warning("Pending state expired for nonce=%s", nonce)
            return None
        return data
    except ClientError as e:
        if e.response["Error"]["Code"] == "ParameterNotFound":
            return None
        raise


def _get_creds(provider: str) -> tuple[str, str]:
    """Fetch clientId + clientSecret from Secrets Manager."""
    resp = _sm.get_secret_value(SecretId=f"/{_STACK}/oauth-creds/{provider}")
    data = json.loads(resp["SecretString"])
    return data["clientId"], data["clientSecret"]


def _exchange_code(
    provider: str,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> dict:
    token_url = _TOKEN_URLS.get(provider)
    if not token_url:
        raise ValueError(f"No token URL configured for provider: {provider}")

    data = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }
    ).encode()
    req = urllib.request.Request(
        token_url,
        data=data,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
            result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Token exchange failed {e.code}: {e.read().decode()}"
        ) from e

    if "error" in result:
        raise RuntimeError(
            f"Token exchange error: {result['error']} — {result.get('error_description', '')}"
        )
    return result


def _store_token(provider: str, user_id: str, token_data: dict) -> None:
    expires_in = token_data.get("expires_in")
    normalized = {
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token"),
        "expires_at": int(time.time()) + int(expires_in) if expires_in else None,
        "token_type": token_data.get("token_type", "bearer"),
        "scopes": token_data.get("scope", "").split()
        if token_data.get("scope")
        else [],
    }
    _ssm.put_parameter(
        Name=f"/{_STACK}/oauth-token/{provider}/{user_id}",
        Value=json.dumps(normalized),
        Type="SecureString",
        Overwrite=True,
        Description=f"OAuth token for provider={provider}",
    )
    logger.info("Stored %s token for user %s", provider, user_id)  # nosec


def handler(event, context):
    params = event.get("queryStringParameters") or {}
    code = params.get("code", "")
    nonce = params.get("state", "")
    error = params.get("error", "")
    error_description = params.get("error_description", "")

    if error:
        logger.warning("Provider returned error: %s — %s", error, error_description)
        return _page(
            400,
            "Authorization Failed",
            f"The provider returned an error: {error}. Please try again from the agent.",
        )

    if not code or not nonce:
        return _page(400, "Bad Request", "Missing required parameters.")

    pending = _pop_pending(nonce)
    if not pending:
        return _page(
            400,
            "Session Expired",
            "This authorization link has expired or already been used. "
            "Please start the connection again from the agent.",
        )

    provider = pending.get("provider", "")
    user_id = pending.get("user_id", "")

    if not provider or not user_id:
        logger.error(
            "Pending state missing provider or user_id: %s", list(pending.keys())
        )
        return _page(
            500, "Internal Error", "Malformed session state. Please try again."
        )

    try:
        client_id, client_secret = _get_creds(provider)
        token_data = _exchange_code(
            provider,
            client_id,
            client_secret,
            code,
            pending["redirect_uri"],
            pending["code_verifier"],
        )
        _store_token(provider, user_id, token_data)
    except Exception as e:
        logger.error("Failed to complete %s auth for user %s: %s", provider, user_id, e)
        return _page(
            500,
            "Authorization Failed",
            "Something went wrong completing your authorization. Please try again.",
        )

    provider_display = provider.capitalize()
    return _page(
        200,
        f"✅ {provider_display} Connected",
        f"Your {provider_display} account is now linked to AgentSOAR. "
        "Return to the agent to continue.",
    )


def _page(status: int, heading: str, body: str) -> dict:
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentSOAR — {heading}</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      max-width: 480px; margin: 120px auto; text-align: center; color: #1a1a1a;
    }}
    h2 {{ font-size: 1.5rem; margin-bottom: 0.5rem; }}
    p {{ color: #555; }}
  </style>
</head>
<body>
  <h2>{heading}</h2>
  <p>{body}</p>
</body>
</html>"""
    return {
        "statusCode": status,
        "headers": {"Content-Type": "text/html; charset=utf-8"},
        "body": html,
    }
