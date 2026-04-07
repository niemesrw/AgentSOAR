# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for gateway/tools/github/github_lambda.py"""

import json
import sys
import urllib.error
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "gateway/tools/github")
import github_lambda as gh

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(custom: dict | None = None):
    ctx = SimpleNamespace()
    if custom is not None:
        ctx.client_context = SimpleNamespace(custom=custom)
    else:
        ctx.client_context = None
    return ctx


def _tool_context(tool_name: str, extra: dict | None = None) -> SimpleNamespace:
    custom = {"bedrockAgentCoreToolName": f"target___{tool_name}"}
    if extra:
        custom.update(extra)
    return _make_context(custom)


# ---------------------------------------------------------------------------
# _get_access_token
# ---------------------------------------------------------------------------


class TestGetAccessToken:
    def test_from_client_context_camel(self):
        ctx = _make_context({"accessToken": "tok123"})
        assert gh._get_access_token({}, ctx) == "tok123"

    def test_from_client_context_snake(self):
        ctx = _make_context({"access_token": "tok456"})
        assert gh._get_access_token({}, ctx) == "tok456"

    def test_from_event_camel(self):
        ctx = _make_context({})
        assert gh._get_access_token({"accessToken": "tok789"}, ctx) == "tok789"

    def test_from_event_snake(self):
        ctx = _make_context({})
        assert gh._get_access_token({"access_token": "tokABC"}, ctx) == "tokABC"

    def test_no_client_context(self):
        ctx = _make_context(None)
        assert gh._get_access_token({"accessToken": "tok"}, ctx) == "tok"

    def test_missing_token_raises(self):
        ctx = _make_context({})
        with pytest.raises(RuntimeError, match="No GitHub access token"):
            gh._get_access_token({}, ctx)


# ---------------------------------------------------------------------------
# handler — routing and tool dispatch
# ---------------------------------------------------------------------------


class TestHandler:
    def test_unknown_tool_returns_error(self):
        ctx = _tool_context("no_such_tool", {"accessToken": "tok"})
        result = gh.handler({}, ctx)
        assert "error" in result
        assert "Unknown tool" in result["error"]

    def test_malformed_tool_name_returns_error(self):
        ctx = _make_context(
            {"bedrockAgentCoreToolName": "notooldelimiter", "accessToken": "tok"}
        )
        result = gh.handler({}, ctx)
        assert "error" in result
        assert "Malformed" in result["error"]

    def test_missing_token_surfaces_as_error(self):
        ctx = _tool_context("github_list_issues")  # no token
        result = gh.handler({"owner": "o", "repo": "r"}, ctx)
        assert "error" in result
        assert "access token" in result["error"]

    @patch("github_lambda._github_request")
    def test_list_issues_success(self, mock_req):
        mock_req.return_value = [
            {"number": 1, "state": "open", "title": "Bug", "html_url": "https://gh/1"}
        ]
        ctx = _tool_context("github_list_issues", {"accessToken": "tok"})
        result = gh.handler({"owner": "acme", "repo": "proj", "state": "open"}, ctx)
        assert result["content"][0]["text"] == "#1 [open] Bug — https://gh/1"

    @patch("github_lambda._github_request")
    def test_create_issue_success(self, mock_req):
        mock_req.return_value = {"number": 42, "html_url": "https://gh/42"}
        ctx = _tool_context("github_create_issue", {"accessToken": "tok"})
        result = gh.handler(
            {"owner": "acme", "repo": "proj", "title": "T", "body": "B"}, ctx
        )
        assert "42" in result["content"][0]["text"]

    @patch("github_lambda._github_request")
    def test_search_issues_empty(self, mock_req):
        mock_req.return_value = {"total_count": 0, "items": []}
        ctx = _tool_context("github_search_issues", {"accessToken": "tok"})
        result = gh.handler({"query": "is:open label:bug"}, ctx)
        assert result["content"][0]["text"] == "No results found."


# ---------------------------------------------------------------------------
# _github_request error handling
# ---------------------------------------------------------------------------


class TestGithubRequest:
    @patch("urllib.request.urlopen")
    def test_successful_get(self, mock_urlopen):
        payload = json.dumps({"id": 1}).encode()
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = payload
        mock_urlopen.return_value = mock_resp
        result = gh._github_request("GET", "/repos/a/b/issues", "tok")
        assert result == {"id": 1}

    @patch("urllib.request.urlopen")
    def test_http_401_raises_runtime_error(self, mock_urlopen):
        err = urllib.error.HTTPError(
            url="https://api.github.com/test",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=BytesIO(b"bad credentials"),
        )
        mock_urlopen.side_effect = err
        with pytest.raises(RuntimeError, match="401"):
            gh._github_request("GET", "/user", "bad-token")

    @patch("urllib.request.urlopen")
    def test_http_404_raises_runtime_error(self, mock_urlopen):
        err = urllib.error.HTTPError(
            url="https://api.github.com/test",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=BytesIO(b"not found"),
        )
        mock_urlopen.side_effect = err
        with pytest.raises(RuntimeError, match="404"):
            gh._github_request("GET", "/repos/x/y", "tok")
