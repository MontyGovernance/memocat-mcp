#!/bin/sh
set -eu

cd "$(dirname "$0")"

python3 scripts/check_mcpb_release.py
npm exec --yes @anthropic-ai/mcpb@2.1.2 -- validate manifest.json
mkdir -p dist
npm exec --yes @anthropic-ai/mcpb@2.1.2 -- \
  pack . "dist/memocat-$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])').mcpb"
