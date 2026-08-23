#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
BINARY="$ROOT_DIR/apps/mathagent-tui/target/release/mathagent-tui"

if [[ ! -x "$BINARY" ]]; then
  printf '%s\n' 'MathAgent is not installed. Run: bash scripts/install.sh' >&2
  exit 2
fi

exec "$BINARY" --root "$ROOT_DIR" "$@"
