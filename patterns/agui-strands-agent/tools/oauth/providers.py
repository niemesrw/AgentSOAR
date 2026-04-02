"""OAuth2 provider configurations.

Adding a new provider:
1. Add an OAuthProvider entry to PROVIDERS
2. Create tools/<provider>/strands_tools.py with make_<provider>_tools(user_id)
3. Store credentials: scripts/configure-oauth.py --provider <name>
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OAuthProvider:
    name: str
    auth_url: str
    token_url: str
    device_url: str | None  # None if device flow not supported
    supports_refresh: bool
    default_scopes: list[str] = field(default_factory=list)


PROVIDERS: dict[str, OAuthProvider] = {
    "github": OAuthProvider(
        name="github",
        auth_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        device_url="https://github.com/login/device/code",
        supports_refresh=False,  # GitHub user tokens don't expire
        default_scopes=["repo", "read:user"],
    ),
    "slack": OAuthProvider(
        name="slack",
        auth_url="https://slack.com/oauth/v2/authorize",
        token_url="https://slack.com/api/oauth.v2.access",
        device_url=None,
        supports_refresh=True,
        default_scopes=["channels:read", "chat:write", "users:read"],
    ),
    "gmail": OAuthProvider(
        name="gmail",
        auth_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        device_url=None,
        supports_refresh=True,
        default_scopes=["https://www.googleapis.com/auth/gmail.readonly"],
    ),
}


def get_provider(name: str) -> OAuthProvider:
    if name not in PROVIDERS:
        raise ValueError(f"Unknown OAuth provider: '{name}'. Known: {list(PROVIDERS)}")
    return PROVIDERS[name]
