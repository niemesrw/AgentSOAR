#!/usr/bin/env python3
"""
Publish a blog post from this repo to ryanniemes.com.

Triggers the publish-post workflow on niemesrw/ryanniemes.com via
repository_dispatch. The ryanniemes.com workflow handles ingestion,
image copying, committing, and deploying.

Usage:
    python scripts/publish_post.py blog/01-building-agentic-soar.md
    python scripts/publish_post.py blog/01-building-agentic-soar.md --draft
    python scripts/publish_post.py blog/01-building-agentic-soar.md --tags aws,security,ai

Requirements:
    - gh CLI installed and authenticated
    - PUBLISH_TOKEN or default gh credentials with repo scope on niemesrw/ryanniemes.com

This pattern is intentionally minimal — all publish logic lives in
ryanniemes.com/.github/workflows/publish-post.yml and scripts/ingest_post.py.
To publish from a different repo, copy this script unchanged.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

SITE_REPO = "niemesrw/ryanniemes.com"
SOURCE_REPO = "niemesrw/AgentSOAR"


def get_current_ref() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True, text=True, cwd=Path(__file__).parent.parent
    )
    return result.stdout.strip() if result.returncode == 0 else "main"


def main():
    parser = argparse.ArgumentParser(description="Publish a blog post to ryanniemes.com")
    parser.add_argument("post", help="Post path relative to repo root, e.g. blog/01-post.md")
    parser.add_argument("--draft", action="store_true", help="Publish as draft (opens PR instead of committing)")
    parser.add_argument("--tags", default="aws,security,ai", help="Comma-separated tags")
    parser.add_argument("--ref", default=None, help="Git ref to use (default: current HEAD)")
    args = parser.parse_args()

    post_path = Path(__file__).parent.parent / args.post
    if not post_path.exists():
        print(f"ERROR: Post not found: {post_path}", file=sys.stderr)
        sys.exit(1)

    ref = args.ref or get_current_ref()

    payload = {
        "event_type": "publish-post",
        "client_payload": {
            "source_repo": SOURCE_REPO,
            "post_path": args.post,
            "tags": args.tags,
            "draft": args.draft,
            "ref": ref,
        },
    }

    print(f"Dispatching publish-post to {SITE_REPO}...")
    print(f"  post:   {args.post}")
    print(f"  ref:    {ref}")
    print(f"  tags:   {args.tags}")
    print(f"  draft:  {args.draft}")

    result = subprocess.run(
        ["gh", "api", f"repos/{SITE_REPO}/dispatches",
         "--input", "-"],
        input=json.dumps(payload),
        capture_output=True, text=True
    )

    if result.returncode != 0:
        print(f"ERROR: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    print(f"\nDispatched. Check progress at:")
    print(f"  https://github.com/{SITE_REPO}/actions")


if __name__ == "__main__":
    main()
