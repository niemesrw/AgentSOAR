#!/usr/bin/env -S uv run
"""
Configure OAuth credentials for AgentSOAR third-party integrations.

Reads stack_name_base from infra-cdk/config.yaml, then prompts for the
OAuth App credentials and writes them to Secrets Manager at:
    /{stack_name}/oauth-creds/{provider}

Supported providers: github, slack, gmail (add more in tools/oauth/providers.py)

Idempotent — safe to run multiple times. Re-running updates the credentials.

Usage:
    uv run scripts/configure-oauth.py
    uv run scripts/configure-oauth.py --provider github
"""

# /// script
# dependencies = ["boto3", "pyyaml", "colorama"]
# ///

import argparse
import getpass
import json
import os
import subprocess
import sys
from pathlib import Path

import boto3
import yaml
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).parent))
from utils import print_msg, print_section

KNOWN_PROVIDERS = {
    "github": {
        "label": "GitHub",
        "needs_secret": True,
        "device_flow": True,
        "note": (
            "Create an OAuth App at: github.com → Settings → Developer settings → OAuth Apps\n"
            "  Enable 'Device Flow' in the app settings.\n"
            "  For web flow only: register the OAuthCallbackUrl stack output as the callback URL."
        ),
    },
    "slack": {
        "label": "Slack",
        "needs_secret": True,
        "device_flow": False,
        "note": (
            "Create a Slack App at: api.slack.com/apps\n"
            "  Add OAuth redirect URL: <OAuthCallbackUrl stack output>"
        ),
    },
    "gmail": {
        "label": "Gmail (Google)",
        "needs_secret": True,
        "device_flow": False,
        "note": (
            "Create OAuth credentials at: console.cloud.google.com → APIs & Services → Credentials\n"
            "  Add authorized redirect URI: <OAuthCallbackUrl stack output>"
        ),
    },
}


def _sso_login() -> None:
    profile = os.environ.get("AWS_PROFILE")
    cmd = ["aws", "sso", "login"]
    if profile:
        cmd += ["--profile", profile]
    print_msg(f"Running: {' '.join(cmd)}", "info")
    result = subprocess.run(cmd)  # noqa: S603
    if result.returncode != 0:
        print_msg("SSO login failed. Please run it manually and retry.", "error")
        sys.exit(1)


def _ensure_aws_config() -> None:
    session = boto3.session.Session()

    if not os.environ.get("AWS_PROFILE") and not os.environ.get("AWS_DEFAULT_PROFILE"):
        available = session.available_profiles
        if available:
            print_msg(f"Available profiles: {', '.join(available)}", "info")
        profile = input("  AWS profile (leave blank for default): ").strip()
        if profile:
            os.environ["AWS_PROFILE"] = profile
            session = boto3.session.Session(profile_name=profile)

    if not session.region_name:
        print_msg("No AWS region detected.", "info")
        region = input("  AWS region (e.g. us-east-1): ").strip()
        if not region:
            print_msg("Region is required.", "error")
            sys.exit(1)
        os.environ["AWS_DEFAULT_REGION"] = region
        os.environ["AWS_REGION"] = region

    try:
        session.client("sts").get_caller_identity()
    except Exception as e:
        msg = str(e).lower()
        if "token" in msg and ("expired" in msg or "sso" in msg or "refresh" in msg):
            print_msg("SSO token expired — triggering login.", "info")
            _sso_login()
        else:
            print_msg(f"AWS credential error: {e}", "error")
            sys.exit(1)


def _load_stack_name() -> str:
    config_path = Path(__file__).parent.parent / "infra-cdk" / "config.yaml"
    if not config_path.exists():
        print_msg(f"config.yaml not found at {config_path}", "error")
        sys.exit(1)
    with open(config_path) as f:
        config = yaml.safe_load(f)
    stack_name = config.get("stack_name_base")
    if not stack_name:
        print_msg("'stack_name_base' not found in config.yaml", "error")
        sys.exit(1)
    return stack_name


def _pick_provider(arg: str | None) -> str:
    if arg:
        if arg not in KNOWN_PROVIDERS:
            print_msg(
                f"Unknown provider '{arg}'. Known: {list(KNOWN_PROVIDERS)}", "error"
            )
            sys.exit(1)
        return arg

    print_section("Select a provider")
    for i, (key, info) in enumerate(KNOWN_PROVIDERS.items(), 1):
        flows = []
        if info["device_flow"]:
            flows.append("device flow")
        flows.append("web flow")
        print(f"  {i}. {info['label']} ({', '.join(flows)})")
    print()

    while True:
        choice = input("  Provider number or name: ").strip()
        if choice in KNOWN_PROVIDERS:
            return choice
        try:
            idx = int(choice) - 1
            return list(KNOWN_PROVIDERS.keys())[idx]
        except (ValueError, IndexError):
            print_msg("Invalid choice — enter a number or provider name.", "error")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Configure OAuth credentials for AgentSOAR"
    )
    parser.add_argument("--provider", help="Provider name (github, slack, gmail, ...)")
    args = parser.parse_args()

    print_section("AgentSOAR — OAuth Credentials Configuration")

    _ensure_aws_config()

    stack_name = _load_stack_name()
    provider = _pick_provider(args.provider)
    info = KNOWN_PROVIDERS[provider]
    secret_name = f"/{stack_name}/oauth-creds/{provider}"

    print_section(f"Configure {info['label']} OAuth App")
    print(f"  {info['note']}")
    print()

    client_id = input("  Client ID: ").strip()
    if not client_id:
        print_msg("Client ID cannot be empty.", "error")
        sys.exit(1)

    client_secret = None
    if info["needs_secret"]:
        client_secret = getpass.getpass("  Client Secret (hidden): ").strip()
        if not client_secret:
            print_msg("Client Secret cannot be empty.", "error")
            sys.exit(1)

    print_section("Writing credentials to Secrets Manager")

    sm = boto3.client("secretsmanager")
    secret_value = json.dumps({"clientId": client_id, "clientSecret": client_secret})

    try:
        sm.put_secret_value(SecretId=secret_name, SecretString=secret_value)
        print_msg(f"Updated: {secret_name}", "success")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            sm.create_secret(
                Name=secret_name,
                Description=f"{info['label']} OAuth App credentials for AgentSOAR",
                SecretString=secret_value,
            )
            print_msg(f"Created: {secret_name}", "success")
        else:
            print_msg(f"Failed to write secret: {e}", "error")
            sys.exit(1)

    print_section("Next steps")
    if info["device_flow"]:
        print(f"  {info['label']} uses device flow — no callback URL to register.")
        print("  In the agent, say: 'connect github' or call github_connect.")
        print("  The agent will give you a code to enter at github.com/login/device.")
    else:
        print("  1. Deploy CDK to get the OAuthCallbackUrl stack output:")
        print()
        print("       cd infra-cdk && npx cdk deploy")
        print()
        print(
            "  2. Copy 'OAuthCallbackUrl' from the stack outputs and register it as the"
        )
        print(f"     redirect/callback URL in your {info['label']} OAuth App.")
        print()
        print(f"  3. In the agent, say: 'connect {provider}'")
        print("     You'll get an authorization URL to open in your browser.")
    print()


if __name__ == "__main__":
    main()
