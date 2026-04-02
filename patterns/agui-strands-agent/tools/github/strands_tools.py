"""GitHub Strands tools backed by the generic OAuth module.

Usage:
    from tools.github.strands_tools import make_github_tools
    tools = make_github_tools(user_id)   # returns list of @tool functions
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from strands import tool

from tools.oauth import (
    DeviceFlowDenied,
    DeviceFlowExpired,
    disconnect,
    get_valid_token,
    poll_device_flow,
    start_device_flow,
)

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
PROVIDER = "github"


def _gh(method: str, path: str, token: str, body: dict | None = None) -> dict:
    url = f"{GITHUB_API}{path}"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body else None,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
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
        body_text = e.read().decode()
        if e.code == 401:
            raise RuntimeError(
                "GitHub token invalid or revoked. Run github_connect to re-authenticate."
            ) from e
        raise RuntimeError(f"GitHub API error {e.code}: {body_text}") from e


def make_github_tools(user_id: str) -> list:
    """Return a list of Strands @tool functions for GitHub, bound to user_id."""

    def _token() -> str:
        tok = get_valid_token(PROVIDER, user_id)
        if not tok:
            raise RuntimeError(
                "GitHub is not connected. Call github_connect to authenticate first."
            )
        return tok

    @tool
    def github_connect() -> str:
        """
        Connect your GitHub account using device authorization.

        On first call: returns a short code and URL — open the URL and enter the code
        to authorize. On the next call: confirms the connection is complete.

        Safe to call at any time — returns immediately if already connected.
        """
        # Already connected?
        tok = get_valid_token(PROVIDER, user_id)
        if tok:
            return "GitHub is already connected. Your GitHub tools are ready to use."

        # Pending flow — try polling first
        try:
            access_token = poll_device_flow(PROVIDER, user_id)
            if access_token:
                return (
                    "GitHub is now connected! Your GitHub tools are ready to use. "
                    "Try: 'list my open issues in owner/repo'"
                )
            return (
                "Still waiting for authorization. Visit the URL and enter the code shown "
                "earlier, then call github_connect again to confirm."
            )
        except RuntimeError:
            pass  # No pending flow — start fresh
        except DeviceFlowExpired:
            pass  # Expired — fall through to start a new flow
        except DeviceFlowDenied:
            return (
                "GitHub authorization was denied. Call github_connect to start again."
            )

        # Start a new device flow
        result = start_device_flow(PROVIDER, user_id)
        user_code = result["user_code"]
        verification_uri = result.get(
            "verification_uri", "https://github.com/login/device"
        )
        expires_in = result.get("expires_in", 900)

        return (
            f"**GitHub Authorization**\n\n"
            f"1. Open: {verification_uri}\n"
            f"2. Enter code: `{user_code}`\n"
            f"3. Click **Authorize**\n"
            f"4. Come back here and call **github_connect** again to confirm\n\n"
            f"_(Code expires in {expires_in // 60} minutes)_"
        )

    @tool
    def github_disconnect() -> str:
        """Disconnect your GitHub account and remove your stored credentials."""
        removed = disconnect(PROVIDER, user_id)
        return (
            "GitHub account disconnected." if removed else "GitHub was not connected."
        )

    @tool
    def github_list_issues(
        owner: str, repo: str, state: str = "open", labels: str = ""
    ) -> str:
        """List issues in a GitHub repository.

        Args:
            owner: Repository owner (user or org)
            repo: Repository name
            state: Filter by state — open, closed, or all
            labels: Comma-separated label names to filter by
        """
        path = f"/repos/{owner}/{repo}/issues?state={state}&per_page=20"
        if labels:
            path += f"&labels={urllib.parse.quote(labels)}"
        issues = _gh("GET", path, _token())
        if not issues:
            return "No issues found."
        return "\n".join(
            f"#{i['number']} [{i['state']}] {i['title']} — {i['html_url']}"
            for i in issues
        )

    @tool
    def github_create_issue(
        owner: str,
        repo: str,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> str:
        """Create a new GitHub issue."""
        payload: dict = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        result = _gh("POST", f"/repos/{owner}/{repo}/issues", _token(), payload)
        return f"Created issue #{result['number']}: {result['html_url']}"

    @tool
    def github_update_issue(
        owner: str,
        repo: str,
        issue_number: int,
        state: str | None = None,
        labels: list[str] | None = None,
        body: str | None = None,
    ) -> str:
        """Update a GitHub issue — close, reopen, relabel, or edit the body."""
        payload: dict = {}
        if state:
            payload["state"] = state
        if labels is not None:
            payload["labels"] = labels
        if body:
            payload["body"] = body
        result = _gh(
            "PATCH", f"/repos/{owner}/{repo}/issues/{issue_number}", _token(), payload
        )
        return f"Updated issue #{result['number']}: {result['html_url']}"

    @tool
    def github_add_comment(owner: str, repo: str, issue_number: int, body: str) -> str:
        """Add a comment to a GitHub issue or pull request."""
        result = _gh(
            "POST",
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            _token(),
            {"body": body},
        )
        return f"Added comment: {result['html_url']}"

    @tool
    def github_create_pr(
        owner: str, repo: str, title: str, body: str, head: str, base: str
    ) -> str:
        """Create a GitHub pull request.

        Args:
            head: Branch containing your changes (e.g. 'feature/my-fix')
            base: Branch to merge into (e.g. 'main')
        """
        result = _gh(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            _token(),
            {"title": title, "body": body, "head": head, "base": base},
        )
        return f"Created PR #{result['number']}: {result['html_url']}"

    @tool
    def github_search_issues(query: str) -> str:
        """Search GitHub issues and PRs. Supports full GitHub search syntax.

        Example queries:
            'is:issue is:open label:bug repo:owner/repo'
            'is:pr is:merged author:username'
        """
        encoded = urllib.parse.quote(query)
        result = _gh("GET", f"/search/issues?q={encoded}&per_page=10", _token())
        items = result.get("items", [])
        if not items:
            return "No results found."
        lines = [
            f"#{i['number']} {i['title']} "
            f"({i['repository_url'].split('repos/')[-1]}) — {i['html_url']}"
            for i in items
        ]
        return (
            f"Found {result['total_count']} results (showing {len(items)}):\n"
            + "\n".join(lines)
        )

    return [
        github_connect,
        github_disconnect,
        github_list_issues,
        github_create_issue,
        github_update_issue,
        github_add_comment,
        github_create_pr,
        github_search_issues,
    ]
