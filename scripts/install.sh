#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v node >/dev/null 2>&1; then
  printf '%s\n' 'Node.js 22+ is required. Install Node.js, then rerun: bash scripts/install.sh' >&2
  exit 2
fi
if ! command -v pnpm >/dev/null 2>&1; then
  printf '%s\n' 'pnpm is required. Install pnpm, then rerun: bash scripts/install.sh' >&2
  exit 2
fi

cd "$ROOT_DIR"
printf '%s\n' '→ installing the locked TypeScript workspace'
pnpm install --frozen-lockfile

printf '%s\n' '→ building the TypeScript proof core and standalone GUI'
pnpm run build

printf '%s\n' ''
printf '%s\n' 'Math Research Agent is installed.'
printf '%s\n' 'Start it with: bash scripts/start.sh'
printf '%s\n' 'Or start immediately with: bash scripts/install.sh --launch'

if [[ "${1:-}" == "--launch" ]]; then
  exec "$ROOT_DIR/scripts/start.sh"
fi
