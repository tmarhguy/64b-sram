#!/usr/bin/env bash
# Run .github/workflows/latex-to-pdf.yml locally with https://github.com/nektos/act
# Prerequisites: brew install act docker; Podman: podman machine start
# Note: xu-cheng/latex-action starts a nested TeX container; use --privileged if you see
#       "permission denied" on /var/run/docker.sock inside the job.
# For a simpler path that matches CI without act, use: ./scripts/run-latex-ci-local.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export PATH="/opt/homebrew/bin:${PATH:-}"

EMPTY_CFG="${TMPDIR:-/tmp}/docker-act-empty-$$"
mkdir -p "$EMPTY_CFG"
echo '{}' >"$EMPTY_CFG/config.json"
export DOCKER_CONFIG="$EMPTY_CFG"

if [[ -z "${DOCKER_HOST:-}" ]] && [[ -S /var/run/docker.sock ]]; then
  export DOCKER_HOST="unix:///var/run/docker.sock"
fi

exec act workflow_dispatch -j build-and-commit-pdf \
  --bind \
  --privileged \
  -P ubuntu-latest=catthehacker/ubuntu:act-latest \
  --container-architecture linux/amd64 \
  "$@"
