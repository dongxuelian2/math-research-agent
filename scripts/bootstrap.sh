#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
OPENPROVER_DIR="$ROOT_DIR/openprover"
PROJECT_DIR="${OBSERVATORY_PROJECT:-$ROOT_DIR/projects/observatory-demo}"
HOST="${OBSERVATORY_HOST:-127.0.0.1}"
PORT="${OBSERVATORY_PORT:-8765}"

if [[ -z "${BASH_VERSION:-}" ]]; then
  printf '%s\n' 'This initializer must run under Bash.' >&2
  exit 2
fi
if ! command -v uv >/dev/null 2>&1; then
  printf '%s\n' 'uv is required. Install uv, then rerun: bash scripts/bootstrap.sh' >&2
  exit 2
fi

cd "$OPENPROVER_DIR"

# Keep uv's cache inside the checkout so the command is reproducible on a
# fresh machine without relying on a pre-existing user Python environment.
UV_CACHE_DIR="${UV_CACHE_DIR:-$ROOT_DIR/.uv-cache}"
export UV_CACHE_DIR
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"

printf '%s\n' '→ syncing the uv environment'
uv sync --extra dev

printf '%s\n' '→ generating the hidden-defect repair showcase'
uv run python -m openprover.math_research demo --project "$PROJECT_DIR"

printf '%s\n' "→ starting the Research Observatory at http://$HOST:$PORT"
exec uv run python -m openprover.math_research observatory \
  --project "$PROJECT_DIR" \
  --host "$HOST" \
  --port "$PORT"
