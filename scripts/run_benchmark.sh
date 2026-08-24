#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
MANIFEST="${1:-$ROOT_DIR/configs/benchmarks/gemini-observatory-v1.json}"
CONFIG="${MATH_RESEARCH_CONFIG:-$ROOT_DIR/configs/models.toml}"
OUTPUT="${BENCHMARK_OUTPUT:-$ROOT_DIR/benchmark-results/$(date +%Y%m%d-%H%M%S)}"

if [[ ! -f "$MANIFEST" ]]; then
  printf 'benchmark manifest not found: %s\n' "$MANIFEST" >&2
  exit 2
fi
if [[ ! -f "$CONFIG" ]]; then
  printf 'Model config not found: %s\n' "$CONFIG" >&2
  exit 2
fi

cd -- "$ROOT_DIR"
uv run python -m math_research_agent.research benchmark \
  --manifest "$MANIFEST" \
  --config "$CONFIG" \
  --output "$OUTPUT"
