# CLAUDE.md — AgentSOAR

## Workflow

- Always create a PR after committing — never push directly to `main`.
- Keep PRs small and focused (one logical change per PR).
- Include the blog post update in the same PR as the code it documents.
- Before pushing Python changes, run `ruff check && ruff format --check` (install via `brew install ruff`).

## Project Context

AgentSOAR is a fork of [FAST](https://github.com/awslabs/fullstack-solution-template-for-agentcore) being built into a modern agentic SOAR (Security Orchestration, Automation, and Response) platform on Amazon Bedrock AgentCore.

The blog documenting the build lives in `blog/`. Update the relevant post in the same PR as the code it describes.

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
