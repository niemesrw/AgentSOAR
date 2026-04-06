# CLAUDE.md — AgentSOAR

## Workflow

- Always create a PR after committing — never push directly to `main`.
- Keep PRs small and focused (one logical change per PR).
- Include the blog post update in the same PR as the code it documents.
- Always use `uv` to run Python tools and scripts (e.g. `uv run pytest`, `uv run ruff check`). Never invoke `python`, `pip`, or `pytest` directly.
- Before pushing Python changes, run `uv run ruff check && uv run ruff format --check`.
- Before pushing changes to `patterns/`, validate A2A API contracts locally:
  ```bash
  uv run pytest tests/unit/test_a2a_contracts.py -q
  ```
  This catches import errors and API signature mismatches without a deploy cycle.
- Pre-commit hooks enforce lint + A2A contracts automatically. Install once with:
  ```bash
  uv run pre-commit install
  ```

## Deploy trigger paths

The Deploy workflow only fires on changes to `infra-cdk/**` or `gateway/**`.
Changes to `patterns/**` (agent code) require a manual deploy trigger:
```bash
gh workflow run deploy.yml --repo niemesrw/AgentSOAR --ref main
```

## Project Context

AgentSOAR is a fork of [FAST](https://github.com/awslabs/fullstack-solution-template-for-agentcore) being built into a modern agentic SOAR (Security Orchestration, Automation, and Response) platform on Amazon Bedrock AgentCore.

The blog documenting the build lives in `blog/`. Update the relevant post in the same PR as the code it describes.

## Local Environment

- Container runtime: **OrbStack** (not Docker Desktop). CDK Python Lambda bundling uses OrbStack's Docker-compatible socket — ensure OrbStack is running before `cdk deploy`.

## AWS Profiles

- **`blanxlait-security`** — primary profile for this project (AgentSOAR deploys in the Security account, 429971481640)
- **`management-admin`** — management/admin account access

When AWS credentials are needed, use `--profile blanxlait-security` (or set `AWS_PROFILE=blanxlait-security`). If the session is expired, run:
```bash
aws sso login --profile blanxlait-security
```

## Architecture

- **Agent pattern**: `agui-strands-agent` (AG-UI streaming, Strands backend)
- **Gateway tools**: Lambda targets in `gateway/tools/<name>/` — one Lambda per tool group, `tool_spec.json` alongside
- **Frontend**: React + AG-UI parser auto-selected by `agui-` prefix in `config.yaml`
- **Infra**: CDK in `infra-cdk/`

## Adding a Gateway Tool

1. Create `gateway/tools/<name>/<name>_lambda.py` following the handler pattern in `gateway/tools/sample_tool/`
2. Create `gateway/tools/<name>/tool_spec.json` with JSON schema definitions
3. Add a `CfnGatewayTarget` in `infra-cdk/lib/backend-stack.ts` referencing the new Lambda
4. Grant `gatewayRole` invoke on the Lambda
5. Update the blog post
