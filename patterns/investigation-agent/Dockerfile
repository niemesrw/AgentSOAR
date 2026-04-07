# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

# Configure UV for container environment
ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    DOCKER_CONTAINER=1 \
    OTEL_PYTHON_LOG_CORRELATION=true \
    PYTHONUNBUFFERED=1

# Copy shared packages for installation
COPY gateway/ gateway/
COPY tools/ agentcore_tools/

# Copy and install agent-specific requirements
COPY patterns/investigation-agent/requirements.txt requirements.txt
RUN uv pip install --no-cache -r requirements.txt && \
    uv pip install --no-cache aws-opentelemetry-distro==0.10.1

# Create non-root user
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

EXPOSE 9000

# Copy agent code — reuse agui-strands-agent tools (gateway client, utils)
COPY patterns/investigation-agent/agent.py .
COPY patterns/investigation-agent/main.py .
COPY patterns/agui-strands-agent/tools/ tools/
COPY patterns/utils/ utils/

# Healthcheck
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9000/ping', timeout=2)" || exit 1

# Start A2A server
CMD ["opentelemetry-instrument", "python", "main.py"]
