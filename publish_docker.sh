#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: Docker is not installed or is not on PATH." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Error: Docker is not running." >&2
  exit 1
fi

if ! docker buildx version >/dev/null 2>&1; then
  echo "Error: Docker Buildx is required." >&2
  exit 1
fi

VERSION="$(sed -nE 's/^version = "([^"]+)"/\1/p' pyproject.toml | head -n 1)"
if [[ -z "$VERSION" ]]; then
  echo "Error: could not read the project version from pyproject.toml." >&2
  exit 1
fi

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]]; then
  echo "Error: invalid project version: $VERSION" >&2
  exit 1
fi

readonly IMAGE="${MEMOCAT_DOCKER_IMAGE:-montygovernance/memocat-mcp}"
readonly REVISION="$(git rev-parse HEAD 2>/dev/null || printf 'unknown')"
readonly BUILDER="memocat-multiarch"

if ! docker buildx inspect "$BUILDER" >/dev/null 2>&1; then
  echo "Creating the reusable $BUILDER Buildx builder..."
  docker buildx create --name "$BUILDER" --driver docker-container >/dev/null
fi

docker buildx inspect "$BUILDER" --bootstrap >/dev/null

echo "Publishing $IMAGE:$VERSION and $IMAGE:latest"
echo "Platforms: linux/amd64, linux/arm64"
echo
echo "Docker Hub authentication is required. If needed, run: docker login"

docker buildx build \
  --builder "$BUILDER" \
  --platform linux/amd64,linux/arm64 \
  --build-arg "VERSION=$VERSION" \
  --build-arg "VCS_REF=$REVISION" \
  --label "org.opencontainers.image.version=$VERSION" \
  --label "org.opencontainers.image.revision=$REVISION" \
  --tag "$IMAGE:$VERSION" \
  --tag "$IMAGE:latest" \
  --push \
  .

echo
echo "Published successfully. Verifying the multi-platform manifest..."
docker buildx imagetools inspect "$IMAGE:$VERSION"
