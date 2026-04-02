"""Generic per-user, per-provider OAuth2 module.

High-level API
--------------
    get_valid_token(provider, user_id)        → access token or None (auto-refreshes)
    start_device_flow(provider, user_id)      → {user_code, verification_uri, ...}
    poll_device_flow(provider, user_id)       → access token or None (still pending)
    start_web_flow(provider, user_id, cb_url) → authorization URL for user to visit
    disconnect(provider, user_id)             → True if token was deleted

Provider credentials (clientId + clientSecret) are stored in Secrets Manager
at /{STACK_NAME}/oauth-creds/{provider}.

Token storage is in SSM SecureString at /{STACK_NAME}/oauth-token/{provider}/{user_id}.
"""

import json
import logging
import os
import time

import boto3

from . import device_flow, store, web_flow
from .device_flow import DeviceFlowDenied, DeviceFlowExpired, DeviceFlowPending
from .providers import get_provider

logger = logging.getLogger(__name__)

_STACK = os.environ.get("STACK_NAME", "AgentSOAR")
_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

_sm = boto3.client("secretsmanager", region_name=_REGION)

__all__ = [
    "get_valid_token",
    "start_device_flow",
    "poll_device_flow",
    "start_web_flow",
    "disconnect",
    "DeviceFlowPending",
    "DeviceFlowExpired",
    "DeviceFlowDenied",
]


def _get_creds(provider: str) -> tuple[str, str | None]:
    """Fetch clientId + optional clientSecret from Secrets Manager."""
    resp = _sm.get_secret_value(SecretId=f"/{_STACK}/oauth-creds/{provider}")
    data = json.loads(resp["SecretString"])
    return data["clientId"], data.get("clientSecret")


def _normalize_token(result: dict, fallback_refresh: str | None = None) -> dict:
    """Normalize a provider token response into our storage format."""
    expires_in = result.get("expires_in")
    return {
        "access_token": result["access_token"],
        "refresh_token": result.get("refresh_token") or fallback_refresh,
        "expires_at": int(time.time()) + int(expires_in) if expires_in else None,
        "token_type": result.get("token_type", "bearer"),
        "scopes": result.get("scope", "").split() if result.get("scope") else [],
    }


def get_valid_token(provider: str, user_id: str) -> str | None:
    """Return a valid access token, refreshing if expired. Returns None if not connected."""
    provider_cfg = get_provider(provider)
    token_data = store.get_token(provider, user_id)
    if not token_data:
        return None
    if not store.is_expired(token_data):
        return token_data["access_token"]

    # Token is expired — try refresh
    if not provider_cfg.supports_refresh or not token_data.get("refresh_token"):
        logger.warning(
            "Token expired for provider=%s user=%s with no refresh token",  # nosec
            provider,
            user_id,
        )
        store.delete_token(provider, user_id)  # Clear stale token
        return None

    try:
        client_id, client_secret = _get_creds(provider)
        refreshed = web_flow.refresh_access_token(
            provider_cfg.token_url,
            client_id,
            client_secret or "",
            token_data["refresh_token"],
        )
        # Keep old refresh token if provider didn't return a new one
        new_token = _normalize_token(refreshed, token_data.get("refresh_token"))
        store.put_token(provider, user_id, new_token)
        logger.info("Refreshed token for provider=%s user=%s", provider, user_id)  # nosec
        return new_token["access_token"]
    except Exception as e:
        logger.error(
            "Token refresh failed for provider=%s user=%s: %s",
            provider,
            user_id,
            e,  # nosec
        )
        store.delete_token(provider, user_id)
        return None


def start_device_flow(
    provider: str, user_id: str, scopes: list[str] | None = None
) -> dict:
    """Start device flow. Returns provider response with user_code + verification_uri."""
    provider_cfg = get_provider(provider)
    if not provider_cfg.device_url:
        raise ValueError(f"Provider '{provider}' does not support device flow")
    client_id, client_secret = _get_creds(provider)
    scopes = scopes or provider_cfg.default_scopes

    result = device_flow.start(provider_cfg.device_url, client_id, scopes)

    store.put_device_pending(
        provider,
        user_id,
        {
            "provider": provider,
            "user_id": user_id,
            "device_code": result["device_code"],
            "client_id": client_id,
            "client_secret": client_secret,
        },
        ttl_seconds=result.get("expires_in", 900),
    )
    return result


def poll_device_flow(provider: str, user_id: str) -> str | None:
    """Poll for device flow completion.

    Returns access token string on success.
    Returns None if still pending (put the state back, user hasn't authorized yet).
    Raises DeviceFlowExpired or DeviceFlowDenied on terminal failure.
    """
    provider_cfg = get_provider(provider)
    pending = store.pop_device_pending(provider, user_id)
    if not pending:
        raise RuntimeError(
            f"No pending device flow for provider={provider} — call start_device_flow first"
        )

    try:
        result = device_flow.poll(
            provider_cfg.token_url,
            pending["client_id"],
            pending["device_code"],
            pending.get("client_secret"),
        )
    except DeviceFlowPending:
        # Restore state — user still hasn't authorized
        store.put_device_pending(provider, user_id, pending)
        return None

    token_data = _normalize_token(result)
    store.put_token(provider, user_id, token_data)
    logger.info("Device flow complete for provider=%s user=%s", provider, user_id)
    return token_data["access_token"]


def start_web_flow(
    provider: str,
    user_id: str,
    callback_url: str,
    scopes: list[str] | None = None,
) -> str:
    """Start web (PKCE) flow. Returns the authorization URL for the user to visit."""
    provider_cfg = get_provider(provider)
    client_id, _ = _get_creds(provider)
    scopes = scopes or provider_cfg.default_scopes

    nonce = web_flow.generate_nonce()
    code_verifier, code_challenge = web_flow.generate_pkce_pair()

    store.put_web_pending(
        nonce,
        {
            "provider": provider,
            "user_id": user_id,
            "code_verifier": code_verifier,
            "redirect_uri": callback_url,
        },
    )

    return web_flow.build_auth_url(
        provider_cfg.auth_url, client_id, callback_url, scopes, nonce, code_challenge
    )


def disconnect(provider: str, user_id: str) -> bool:
    """Remove stored token. Returns True if a token was deleted."""
    # Also clean up any pending device flow
    store.pop_device_pending(provider, user_id)
    return store.delete_token(provider, user_id)
