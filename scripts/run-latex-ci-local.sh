#!/usr/bin/env bash
# Same TeX setup as .github/workflows (xu-cheng/latex-action -> ghcr.io/xu-cheng/texlive-full).
# Run from repo root: ./scripts/run-latex-ci-local.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if command -v docker >/dev/null 2>&1; then
  DOCKER=(docker)
elif command -v podman >/dev/null 2>&1; then
  DOCKER=(podman)
else
  echo "Install Docker Desktop or: brew install docker podman && podman machine start" >&2
  exit 1
fi

# Avoid Docker Desktop credential helper when using Podman.
export DOCKER_CONFIG="${DOCKER_CONFIG:-}"
if [[ -z "${DOCKER_CONFIG:-}" ]] && [[ -f "$HOME/.docker/config.json" ]] && grep -q credsStore "$HOME/.docker/config.json" 2>/dev/null; then
  export DOCKER_CONFIG="${TMPDIR:-/tmp}/docker-empty-config-$$"
  mkdir -p "$DOCKER_CONFIG"
  echo '{}' >"$DOCKER_CONFIG/config.json"
fi

IMAGE="${LATEX_IMAGE:-ghcr.io/xu-cheng/texlive-full:latest}"
PLATFORM="${LATEX_PLATFORM:-}"
if [[ "$(uname -m)" == "arm64" ]] || [[ "$(uname -m)" == "aarch64" ]]; then
  PLATFORM="${PLATFORM:-linux/amd64}"
fi

echo "Using image: $IMAGE${PLATFORM:+ (platform $PLATFORM)}"

PULL_ARGS=()
RUN_ARGS=()
if [[ -n "$PLATFORM" ]]; then
  PULL_ARGS+=(--platform "$PLATFORM")
  RUN_ARGS+=(--platform "$PLATFORM")
fi

"${DOCKER[@]}" pull "${PULL_ARGS[@]}" "$IMAGE"
"${DOCKER[@]}" run --rm "${RUN_ARGS[@]}" \
  -v "$ROOT:/workdir" \
  -w /workdir \
  "$IMAGE" \
  latexmk -pdf -file-line-error -halt-on-error -interaction=nonstopmode ESE3700_Proj2_Marhguy.tex

echo "OK: $ROOT/ESE3700_Proj2_Marhguy.pdf"
