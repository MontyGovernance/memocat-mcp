# syntax=docker/dockerfile:1
# MCP is a stdio server, not an HTTP service. Run this image with `-i` (or via
# `docker compose run -T mcp`) and keep Montycat in the companion service.
FROM python:3.12-slim

ARG VERSION=0.4.3
ARG VCS_REF=unknown

LABEL org.opencontainers.image.title="MemoCat MCP" \
      org.opencontainers.image.description="Self-hosted AI agent memory MCP server powered by Montycat" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/MontyGovernance/memocat-mcp" \
      org.opencontainers.image.url="https://github.com/MontyGovernance/memocat-mcp" \
      org.opencontainers.image.documentation="https://github.com/MontyGovernance/memocat-mcp/blob/master/README.md" \
      org.opencontainers.image.licenses="MIT" \
      io.modelcontextprotocol.server.name="io.github.MontyGovernance/memocat-mcp"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEMOCAT_AUTOSTART=off

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY memocat_mcp ./memocat_mcp

RUN pip install --no-cache-dir .

RUN groupadd --system memocat \
    && useradd --system --gid memocat --home-dir /nonexistent --no-create-home memocat

USER memocat

ENTRYPOINT ["memocat-mcp"]
