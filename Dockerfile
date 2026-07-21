# syntax=docker/dockerfile:1
# MCP is a stdio server, not an HTTP service. Run this image with `-i` (or via
# `docker compose run -T mcp`) and keep Montycat in the companion service.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEMOCAT_AUTOSTART=off

WORKDIR /app

COPY pyproject.toml README.md ./
COPY memocat_mcp ./memocat_mcp

# 1.0.7 is currently local-only. The caller supplies that checkout as the
# named `montycat_client` build context; this avoids accidentally building
# against PyPI's older client or downloading an unreviewed source revision.
COPY --from=montycat_client . /opt/montycat-client
RUN pip install --no-cache-dir /opt/montycat-client \
    && pip install --no-cache-dir .

ENTRYPOINT ["memocat-mcp"]
