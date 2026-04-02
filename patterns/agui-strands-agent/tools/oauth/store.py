"""Per-user, per-provider OAuth token storage in SSM SecureString.

Token path:   /{stack}/oauth-token/{provider}/{user_id}   — SecureString
Web pending:  /{stack}/oauth-pending/{nonce}               — String (short-lived)
Device state: /{stack}/oauth-device/{provider}/{user_id}   — String (short-lived)
"""

import json
import logging
import os
import time

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

_STACK = os.environ.get("STACK_NAME", "AgentSOAR")
_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

_ssm = boto3.client("ssm", region_name=_REGION)


# ── Token storage ────────────────────────────────────────────────────────────


def get_token(provider: str, user_id: str) -> dict | None:
    """Return stored token dict or None if not found."""
    try:
        resp = _ssm.get_parameter(
            Name=f"/{_STACK}/oauth-token/{provider}/{user_id}", WithDecryption=True
        )
        return json.loads(resp["Parameter"]["Value"])
    except ClientError as e:
        if e.response["Error"]["Code"] == "ParameterNotFound":
            return None
        raise


def put_token(provider: str, user_id: str, token_data: dict) -> None:
    """Store token dict as SSM SecureString."""
    _ssm.put_parameter(
        Name=f"/{_STACK}/oauth-token/{provider}/{user_id}",
        Value=json.dumps(token_data),
        Type="SecureString",
        Overwrite=True,
        Description=f"OAuth token for provider={provider}",
    )
    logger.info("Stored token for provider=%s user=%s", provider, user_id)  # nosec


def delete_token(provider: str, user_id: str) -> bool:
    """Delete stored token. Returns True if deleted, False if not found."""
    try:
        _ssm.delete_parameter(Name=f"/{_STACK}/oauth-token/{provider}/{user_id}")
        logger.info("Deleted token for provider=%s user=%s", provider, user_id)  # nosec
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ParameterNotFound":
            return False
        raise


def is_expired(token_data: dict, buffer_seconds: int = 60) -> bool:
    """Return True if token will expire within buffer_seconds."""
    expires_at = token_data.get("expires_at")
    if not expires_at:
        return False  # No expiry info → assume valid
    return time.time() >= expires_at - buffer_seconds


# ── Pending state helpers ─────────────────────────────────────────────────────


def _put_pending(name: str, data: dict, ttl_seconds: int) -> None:
    data["_expires_at"] = int(time.time()) + ttl_seconds
    _ssm.put_parameter(Name=name, Value=json.dumps(data), Type="String", Overwrite=True)


def _pop_pending(name: str) -> dict | None:
    try:
        resp = _ssm.get_parameter(Name=name)
        _ssm.delete_parameter(Name=name)
        data = json.loads(resp["Parameter"]["Value"])
        if time.time() > data.get("_expires_at", float("inf")):
            return None
        return data
    except ClientError as e:
        if e.response["Error"]["Code"] == "ParameterNotFound":
            return None
        raise


# ── Web flow pending state ────────────────────────────────────────────────────


def put_web_pending(nonce: str, data: dict, ttl_seconds: int = 600) -> None:
    """Store pending web-flow state keyed by nonce."""
    _put_pending(f"/{_STACK}/oauth-pending/{nonce}", data, ttl_seconds)


def pop_web_pending(nonce: str) -> dict | None:
    """Fetch and delete pending web-flow state. Returns None if not found or expired."""
    return _pop_pending(f"/{_STACK}/oauth-pending/{nonce}")


# ── Device flow pending state ─────────────────────────────────────────────────


def put_device_pending(
    provider: str, user_id: str, data: dict, ttl_seconds: int = 900
) -> None:
    """Store pending device-flow state."""
    _put_pending(f"/{_STACK}/oauth-device/{provider}/{user_id}", data, ttl_seconds)


def pop_device_pending(provider: str, user_id: str) -> dict | None:
    """Fetch and delete pending device-flow state. Returns None if not found or expired."""
    return _pop_pending(f"/{_STACK}/oauth-device/{provider}/{user_id}")
