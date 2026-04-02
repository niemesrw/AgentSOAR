#!/usr/bin/env python3
"""
Publish a blog post from AgentSOAR to ryanniemes.com.

Fires a repository_dispatch to niemesrw/ryanniemes.com, which runs the
publish-post workflow to ingest the post and deploy the site.

Usage:
    uv run scripts/publish_post.py blog/03-guardduty-integration.md --tags aws,security,ai,guardduty
    uv run scripts/publish_post.py blog/03-guardduty-integration.md --tags aws,security --draft

Requires: PUBLISH_TOKEN env var (GitHub PAT with repo scope on niemesrw/ryanniemes.com)
          or the gh CLI to be authenticated.
"""

import argparse
import json
import os
import subprocess  # nosec B404
import sys
from pathlib import Path


TARGET_REPO = "niemesrw/ryanniemes.com"
SOURCE_REPO = "niemesrw/AgentSOAR"


def get_current_ref() -> str:
    result = subprocess.run(  # nosec B603
        ["git", "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def dispatch(post_path: str, tags: str, draft: bool, ref: str) -> None:
    payload = {
        "source_repo": SOURCE_REPO,
        "post_path": post_path,
        "tags": tags,
        "ref": ref,
        "draft": draft,
    }
    token = os.environ.get("PUBLISH_TOKEN")
    if token:
        subprocess.run(  # nosec B603
            [
                "gh", "api",
                f"repos/{TARGET_REPO}/dispatches",
                "--method", "POST",
                "--field", f"event_type=publish-post",
                "--field", f"client_payload={json.dumps(payload)}",
                "--header", f"Authorization: Bearer {token}",
            ],
            check=True,
        )
    else:
        # Fall back to gh CLI auth
        subprocess.run(  # nosec B603
            [
                "gh", "api",
                f"repos/{TARGET_REPO}/dispatches",
                "--method", "POST",
                "--raw-field", f"event_type=publish-post",
                "--raw-field", f"client_payload={json.dumps(payload)}",
            ],
            check=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a blog post to ryanniemes.com")
    parser.add_argument("post", help="Relative path to post (e.g. blog/03-my-post.md)")
    parser.add_argument("--tags", default="", help="Comma-separated tags")
    parser.add_argument("--draft", action="store_true", help="Open a draft PR instead of publishing directly")
    parser.add_argument("--ref", default="", help="Git ref to check out (defaults to current HEAD SHA)")
    args = parser.parse_args()

    post_path = Path(args.post)
    if not post_path.exists():
        print(f"ERROR: Post not found: {post_path}", file=sys.stderr)
        return 1

    ref = args.ref or get_current_ref()
    print(f"Dispatching publish for {args.post} at {ref[:12]}...")
    dispatch(args.post, args.tags, args.draft, ref)
    print(f"Dispatched. Watch: https://github.com/{TARGET_REPO}/actions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
