#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -f "$ROOT_DIR/backend/dist/src/index.js" ]]; then
  printf '%s\n' 'Math Research Agent is not installed. Run: bash scripts/install.sh' >&2
  exit 2
fi

exec pnpm --dir "$ROOT_DIR" start -- "$@"
