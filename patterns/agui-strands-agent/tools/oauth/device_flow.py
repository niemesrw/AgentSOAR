"""Generic OAuth2 Device Authorization Grant (RFC 8628)."""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)


class DeviceFlowPending(Exception):
    """Token not yet issued — user hasn't authorized yet."""


class DeviceFlowExpired(Exception):
    """Device code expired — start a new flow."""


class DeviceFlowDenied(Exception):
    """User explicitly denied the authorization."""


def start(device_url: str, client_id: str, scopes: list[str]) -> dict:
    """POST to provider's device endpoint.

    Returns dict with: device_code, user_code, verification_uri, expires_in, interval
    """
    data = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "scope": " ".join(scopes),
        }
    ).encode()
    req = urllib.request.Request(
        device_url,
        data=data,
        headers={"Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read().decode())

    if "error" in result:
        raise RuntimeError(
            f"Device flow start failed: {result['error']} — {result.get('error_description', '')}"
        )
    return result


def poll(
    token_url: str,
    client_id: str,
    device_code: str,
    client_secret: str | None = None,
) -> dict:
    """Poll the token endpoint for completion.

    Raises DeviceFlowPending if the user hasn't authorized yet.
    Raises DeviceFlowExpired / DeviceFlowDenied on terminal failures.
    Returns token dict on success.
    """
    params: dict = {
        "client_id": client_id,
        "device_code": device_code,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    }
    if client_secret:
        params["client_secret"] = client_secret

    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        token_url,
        data=data,
        headers={"Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read().decode())

    error = result.get("error")
    if not error:
        return result  # success

    if error == "authorization_pending":
        raise DeviceFlowPending()
    if error in ("expired_token", "device_code_expired"):
        raise DeviceFlowExpired()
    if error == "access_denied":
        raise DeviceFlowDenied()
    raise RuntimeError(
        f"Device flow error: {error} — {result.get('error_description', '')}"
    )
