#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v uv >/dev/null 2>&1; then
  printf '%s\n' 'uv is required. Install uv, then rerun: bash scripts/install.sh' >&2
  exit 2
fi
if ! command -v cargo >/dev/null 2>&1; then
  printf '%s\n' 'Rust/Cargo is required. Install Rust, then rerun: bash scripts/install.sh' >&2
  exit 2
fi

cd "$ROOT_DIR"
printf '%s\n' '→ syncing the locked Python research environment'
uv sync --project "$ROOT_DIR" --extra dev --locked

printf '%s\n' '→ building the Rust terminal client'
cargo build --manifest-path "$ROOT_DIR/apps/mathagent-tui/Cargo.toml" --release --locked

printf '%s\n' ''
printf '%s\n' 'MathAgent is installed.'
printf '%s\n' 'Start it with: bash scripts/start.sh'
printf '%s\n' 'Or start immediately with: bash scripts/install.sh --launch'

if [[ "${1:-}" == "--launch" ]]; then
  exec "$ROOT_DIR/scripts/start.sh"
fi
