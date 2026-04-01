#!/usr/bin/env -S uv run
"""
Configure GitHub OAuth credentials for AgentSOAR.

Reads stack_name_base from infra-cdk/config.yaml, then prompts for your
GitHub OAuth App credentials and writes them to the Secrets Manager secret
that CDK created (or will create) at /{stack_name}/github-oauth.

Idempotent: safe to run multiple times. Re-running updates the credentials.

Usage:
    uv run scripts/configure-github-oauth.py

Steps this script walks you through:
    1. Prompts for your GitHub OAuth App clientId and clientSecret
    2. Writes them to Secrets Manager
    3. Tells you to run `cdk deploy` so the Custom Resource creates the
       credential provider and outputs the callback URL to register in GitHub
"""

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
    """Prompt for AWS profile and region if not configured, then validate credentials."""
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


def main() -> None:
    print_section("AgentSOAR — GitHub OAuth Configuration")

    _ensure_aws_config()

    stack_name = _load_stack_name()
    secret_name = f"/{stack_name}/github-oauth"
    print_msg(f"Stack: {stack_name}", "info")
    print_msg(f"Secret: {secret_name}", "info")

    # ── 1. Prompt for credentials ────────────────────────────────────────────
    print_section("Enter your GitHub OAuth App credentials")
    print(
        "  Create an OAuth App at: github.com → Settings → Developer settings → OAuth Apps"
    )
    print("  You'll get the callback URL to register AFTER running `cdk deploy`.")
    print()

    client_id = input("  Client ID: ").strip()
    if not client_id:
        print_msg("Client ID cannot be empty.", "error")
        sys.exit(1)

    client_secret = getpass.getpass("  Client Secret (hidden): ").strip()
    if not client_secret:
        print_msg("Client Secret cannot be empty.", "error")
        sys.exit(1)

    # ── 2. Write to Secrets Manager ─────────────────────────────────────────
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
                Description="GitHub OAuth App credentials (clientId + clientSecret) for AgentSOAR",
                SecretString=secret_value,
            )
            print_msg(f"Created: {secret_name}", "success")
        else:
            print_msg(f"Failed to write secret: {e}", "error")
            sys.exit(1)

    # ── 3. Next steps ────────────────────────────────────────────────────────
    print_section("Next steps")
    print(
        "  1. Deploy CDK — this creates the credential provider and outputs the callback URL:"
    )
    print()
    print("       cd infra-cdk && npx cdk deploy")
    print()
    print("  2. Copy 'GitHubOAuthCallbackUrl' from the stack outputs and paste it into")
    print("     your GitHub OAuth App as the Authorization callback URL:")
    print("       github.com → Settings → Developer settings → OAuth Apps → your app")
    print()
    print("  3. In the SOAR agent chat, say: 'connect GitHub'")
    print("     You'll get an auth URL — click it to authorize.")
    print()


if __name__ == "__main__":
    main()
