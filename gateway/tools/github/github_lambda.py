# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
GitHub gateway tool Lambda — authenticated via AgentCore Identity OAuth2.

Authentication flow:
1. User says "connect GitHub" → agent calls github_connect tool → gets auth URL
2. User authorizes the GitHub OAuth App → token stored in AgentCore token vault
3. On subsequent calls, the agent calls get_resource_oauth2_token to fetch the
   user's cached GitHub token from the vault and passes it to this Lambda via
   the event or client_context (depending on how the agent invokes the gateway tool)

Note: AgentCore Gateway Lambda targets only support GATEWAY_IAM_ROLE as the
credential provider type — the gateway cannot inject OAuth tokens directly.
Token retrieval from the vault is handled by the agent at invocation time.
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger()
logger.setLevel(logging.INFO)

GITHUB_API = "https://api.github.com"


def _get_access_token(event: dict, context) -> str:
    """
    Extract the GitHub access token from the Lambda invocation.

    The agent fetches the token from the AgentCore token vault via
    get_resource_oauth2_token and passes it here through client_context.custom
    or the event payload.
    """
    custom = {}
    if context.client_context and context.client_context.custom:
        custom = context.client_context.custom
        # Log the full custom context (keys only — don't log token values)
        logger.info("Lambda client_context.custom keys: %s", list(custom.keys()))

    token = custom.get("accessToken") or custom.get("access_token")

    if not token:
        token = event.get("accessToken") or event.get("access_token")

    if not token:
        raise RuntimeError(
            "No GitHub access token found. The user must authorize GitHub via the "
            "'github_connect' agent tool before using GitHub tools. "
            f"(Checked client_context.custom keys: {list(custom.keys())})"
        )

    return token


def _github_request(
    method: str, path: str, access_token: str, body: dict | None = None
) -> dict:
    """Make an authenticated GitHub API request using the user's OAuth token."""
    url = f"{GITHUB_API}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "AgentSOAR/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            return json.loads(raw.decode()) if raw else {}
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        if e.code == 401:
            logger.error(
                "GitHub 401 — token may be expired or have insufficient scopes"
            )
        logger.error("GitHub API error %d: %s", e.code, error_body)
        raise RuntimeError(f"GitHub API error {e.code}: {error_body}") from e


def github_create_issue(
    access_token: str,
    owner: str,
    repo: str,
    title: str,
    body: str,
    labels: list[str] | None = None,
) -> str:
    payload: dict = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels
    result = _github_request(
        "POST", f"/repos/{owner}/{repo}/issues", access_token, payload
    )
    return f"Created issue #{result['number']}: {result['html_url']}"


def github_update_issue(
    access_token: str,
    owner: str,
    repo: str,
    issue_number: int,
    state: str | None = None,
    labels: list[str] | None = None,
    body: str | None = None,
) -> str:
    payload: dict = {}
    if state:
        payload["state"] = state
    if labels is not None:
        payload["labels"] = labels
    if body:
        payload["body"] = body
    result = _github_request(
        "PATCH", f"/repos/{owner}/{repo}/issues/{issue_number}", access_token, payload
    )
    return f"Updated issue #{result['number']}: {result['html_url']}"


def github_add_comment(
    access_token: str, owner: str, repo: str, issue_number: int, body: str
) -> str:
    result = _github_request(
        "POST",
        f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
        access_token,
        {"body": body},
    )
    return f"Added comment: {result['html_url']}"


def github_list_issues(
    access_token: str, owner: str, repo: str, state: str = "open", labels: str = ""
) -> str:
    path = f"/repos/{owner}/{repo}/issues?state={state}&per_page=20"
    if labels:
        path += f"&labels={urllib.parse.quote(labels)}"
    issues = _github_request("GET", path, access_token)
    if not issues:
        return "No issues found."
    lines = [
        f"#{i['number']} [{i['state']}] {i['title']} — {i['html_url']}" for i in issues
    ]
    return "\n".join(lines)


def github_create_pr(
    access_token: str,
    owner: str,
    repo: str,
    title: str,
    body: str,
    head: str,
    base: str,
) -> str:
    payload = {"title": title, "body": body, "head": head, "base": base}
    result = _github_request(
        "POST", f"/repos/{owner}/{repo}/pulls", access_token, payload
    )
    return f"Created PR #{result['number']}: {result['html_url']}"


def github_search_issues(access_token: str, query: str) -> str:
    encoded = urllib.parse.quote(query)
    result = _github_request(
        "GET", f"/search/issues?q={encoded}&per_page=10", access_token
    )
    items = result.get("items", [])
    if not items:
        return "No results found."
    lines = [
        f"#{i['number']} {i['title']} ({i['repository_url'].split('repos/')[-1]}) — {i['html_url']}"
        for i in items
    ]
    return (
        f"Found {result['total_count']} results (showing {len(items)}):\n"
        + "\n".join(lines)
    )


TOOL_HANDLERS = {
    "github_create_issue": lambda e, tok: github_create_issue(
        tok, e["owner"], e["repo"], e["title"], e["body"], e.get("labels")
    ),
    "github_update_issue": lambda e, tok: github_update_issue(
        tok,
        e["owner"],
        e["repo"],
        e["issue_number"],
        e.get("state"),
        e.get("labels"),
        e.get("body"),
    ),
    "github_add_comment": lambda e, tok: github_add_comment(
        tok, e["owner"], e["repo"], e["issue_number"], e["body"]
    ),
    "github_list_issues": lambda e, tok: github_list_issues(
        tok, e["owner"], e["repo"], e.get("state", "open"), e.get("labels", "")
    ),
    "github_create_pr": lambda e, tok: github_create_pr(
        tok, e["owner"], e["repo"], e["title"], e["body"], e["head"], e["base"]
    ),
    "github_search_issues": lambda e, tok: github_search_issues(tok, e["query"]),
}


def handler(event, context):
    try:
        delimiter = "___"
        original_tool_name = context.client_context.custom["bedrockAgentCoreToolName"]
        _, sep, tool_name = original_tool_name.partition(delimiter)
        if not sep or not tool_name:
            msg = f"Malformed tool name '{original_tool_name}': missing delimiter '{delimiter}'"
            logger.error(msg)
            return {"error": msg}

        safe_log = {
            k: event[k] for k in ("owner", "repo", "issue_number") if k in event
        }
        logger.info("Processing tool: %s %s", tool_name, safe_log)

        if tool_name not in TOOL_HANDLERS:
            return {
                "error": f"Unknown tool: {tool_name}. Supported: {list(TOOL_HANDLERS.keys())}"
            }

        access_token = _get_access_token(event, context)
        result = TOOL_HANDLERS[tool_name](event, access_token)
        return {"content": [{"type": "text", "text": result}]}

    except Exception as e:
        logger.error("Error processing request: %s", str(e))
        return {"error": str(e)}
