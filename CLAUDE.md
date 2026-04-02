# CLAUDE.md — AgentSOAR

## Workflow

- Always create a PR after committing — never push directly to `main`.
- Keep PRs small and focused (one logical change per PR).
- Include the blog post update in the same PR as the code it documents.
- Before pushing Python changes, run `ruff check && ruff format --check` (install via `brew install ruff`).

## Project Context

AgentSOAR is a fork of [FAST](https://github.com/awslabs/fullstack-solution-template-for-agentcore) being built into a modern agentic SOAR (Security Orchestration, Automation, and Response) platform on Amazon Bedrock AgentCore.

The blog documenting the build lives in `blog/`. Update the relevant post in the same PR as the code it describes.

## Local Environment

- Container runtime: **OrbStack** (not Docker Desktop). CDK Python Lambda bundling uses OrbStack's Docker-compatible socket — ensure OrbStack is running before `cdk deploy`.

## AWS Profiles

- **`blanxlait-ai`** — primary profile for this project (AgentSOAR deploys here)
- **`management-admin`** — management/admin account access

When AWS credentials are needed, use `--profile blanxlait-ai` (or set `AWS_PROFILE=blanxlait-ai`). If the session is expired, run:
```bash
aws sso login --profile blanxlait-ai
```

## Architecture

- **Agent pattern**: `agui-strands-agent` (AG-UI streaming, Strands backend)
- **Gateway tools**: Lambda targets in `gateway/tools/<name>/` — one Lambda per tool group, `tool_spec.json` alongside
- **Frontend**: React + AG-UI parser auto-selected by `agui-` prefix in `config.yaml`
- **Infra**: CDK in `infra-cdk/`

## macOS Gotchas

- **Screenshot filenames**: macOS uses a Unicode narrow no-break space (U+202F) between the time and AM/PM in screenshot filenames (e.g. `Screenshot 2026-04-01 at 2.01.18 PM.png`). Normal `cp` with a regular space will fail. Use: `cp $'...path with \u202f...' destination`

## Publishing a Blog Post

To publish a post from `blog/` to ryanniemes.com:

```bash
python scripts/publish_post.py blog/01-building-agentic-soar.md --tags aws,security,ai
```

This fires a `repository_dispatch` to `niemesrw/ryanniemes.com`. That repo's workflow ingests the post, copies images, commits to main, and Cloudflare Pages deploys automatically.

Add `--draft` to open a PR instead of deploying live.

The `ryanniemes.com` workflow requires a `PUBLISH_TOKEN` secret (a GitHub PAT with `repo` scope on `niemesrw/ryanniemes.com`).

---

## Adding a Gateway Tool

1. Create `gateway/tools/<name>/<name>_lambda.py` following the handler pattern in `gateway/tools/sample_tool/`
2. Create `gateway/tools/<name>/tool_spec.json` with JSON schema definitions
3. Add a `CfnGatewayTarget` in `infra-cdk/lib/backend-stack.ts` referencing the new Lambda
4. Grant `gatewayRole` invoke on the Lambda
5. Update the blog post
