"""OAuth2 Authorization Code flow with PKCE (RFC 7636)."""

import base64
import hashlib
import json
import logging
import secrets
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) using S256 method."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def generate_nonce() -> str:
    return secrets.token_urlsafe(24)


def build_auth_url(
    auth_url: str,
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    state: str,
    code_challenge: str,
) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
        "response_type": "code",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{auth_url}?{urllib.parse.urlencode(params)}"


def exchange_code(
    token_url: str,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> dict:
    """Exchange authorization code + PKCE verifier for tokens."""
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


def refresh_access_token(
    token_url: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> dict:
    """Use a refresh token to obtain a new access token."""
    data = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
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
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Token refresh failed {e.code}: {e.read().decode()}") from e
