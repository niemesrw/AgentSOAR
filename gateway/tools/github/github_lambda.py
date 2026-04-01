# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import logging
import os
import urllib.error
import urllib.request

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

GITHUB_API = "https://api.github.com"
SECRET_ARN = os.environ["GITHUB_PAT_SECRET_ARN"]

_cached_pat = None


def _get_pat() -> str:
    """Fetch GitHub PAT from Secrets Manager (cached per Lambda container)."""
    global _cached_pat
    if _cached_pat is None:
        client = boto3.client("secretsmanager")
        response = client.get_secret_value(SecretId=SECRET_ARN)
        _cached_pat = response["SecretString"].strip()
    return _cached_pat


def _github_request(method: str, path: str, body: dict | None = None) -> dict:
    """Make an authenticated GitHub API request."""
    url = f"{GITHUB_API}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {_get_pat()}",
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
        logger.error(f"GitHub API error {e.code}: {error_body}")
        raise RuntimeError(f"GitHub API error {e.code}: {error_body}") from e


def github_create_issue(
    owner: str, repo: str, title: str, body: str, labels: list[str] | None = None
) -> str:
    payload = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels
    result = _github_request("POST", f"/repos/{owner}/{repo}/issues", payload)
    return f"Created issue #{result['number']}: {result['html_url']}"


def github_update_issue(
    owner: str,
    repo: str,
    issue_number: int,
    state: str | None = None,
    labels: list[str] | None = None,
    body: str | None = None,
) -> str:
    payload = {}
    if state:
        payload["state"] = state
    if labels is not None:
        payload["labels"] = labels
    if body:
        payload["body"] = body
    result = _github_request(
        "PATCH", f"/repos/{owner}/{repo}/issues/{issue_number}", payload
    )
    return f"Updated issue #{result['number']}: {result['html_url']}"


def github_add_comment(owner: str, repo: str, issue_number: int, body: str) -> str:
    result = _github_request(
        "POST", f"/repos/{owner}/{repo}/issues/{issue_number}/comments", {"body": body}
    )
    return f"Added comment: {result['html_url']}"


def github_list_issues(
    owner: str, repo: str, state: str = "open", labels: str = ""
) -> str:
    path = f"/repos/{owner}/{repo}/issues?state={state}&per_page=20"
    if labels:
        path += f"&labels={labels}"
    issues = _github_request("GET", path)
    if not issues:
        return "No issues found."
    lines = [
        f"#{i['number']} [{i['state']}] {i['title']} — {i['html_url']}" for i in issues
    ]
    return "\n".join(lines)


def github_create_pr(
    owner: str, repo: str, title: str, body: str, head: str, base: str
) -> str:
    payload = {"title": title, "body": body, "head": head, "base": base}
    result = _github_request("POST", f"/repos/{owner}/{repo}/pulls", payload)
    return f"Created PR #{result['number']}: {result['html_url']}"


def github_search_issues(query: str) -> str:
    import urllib.parse

    encoded = urllib.parse.quote(query)
    result = _github_request("GET", f"/search/issues?q={encoded}&per_page=10")
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
    "github_create_issue": lambda e: github_create_issue(
        e["owner"], e["repo"], e["title"], e["body"], e.get("labels")
    ),
    "github_update_issue": lambda e: github_update_issue(
        e["owner"],
        e["repo"],
        e["issue_number"],
        e.get("state"),
        e.get("labels"),
        e.get("body"),
    ),
    "github_add_comment": lambda e: github_add_comment(
        e["owner"], e["repo"], e["issue_number"], e["body"]
    ),
    "github_list_issues": lambda e: github_list_issues(
        e["owner"], e["repo"], e.get("state", "open"), e.get("labels", "")
    ),
    "github_create_pr": lambda e: github_create_pr(
        e["owner"], e["repo"], e["title"], e["body"], e["head"], e["base"]
    ),
    "github_search_issues": lambda e: github_search_issues(e["query"]),
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
        logger.info(f"Processing tool: {tool_name} {safe_log}")

        if tool_name not in TOOL_HANDLERS:
            return {
                "error": f"Unknown tool: {tool_name}. Supported: {list(TOOL_HANDLERS.keys())}"
            }

        result = TOOL_HANDLERS[tool_name](event)
        return {"content": [{"type": "text", "text": result}]}

    except Exception as e:
        logger.error(f"Error processing request: {str(e)}")
        return {"error": str(e)}
