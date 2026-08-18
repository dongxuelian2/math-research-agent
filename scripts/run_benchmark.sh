#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
OPENPROVER_DIR="$ROOT_DIR/openprover"
MANIFEST="${1:-$ROOT_DIR/benchmarks/gemini-observatory-v1.json}"
CONFIG="${GEMINI_CONFIG:-$ROOT_DIR/configs/models.gemini.example.json}"
OUTPUT="${BENCHMARK_OUTPUT:-$ROOT_DIR/benchmark-results/$(date +%Y%m%d-%H%M%S)}"

if [[ ! -f "$MANIFEST" ]]; then
  printf 'benchmark manifest not found: %s\n' "$MANIFEST" >&2
  exit 2
fi
if [[ ! -f "$CONFIG" ]]; then
  printf 'Gemini config not found: %s\n' "$CONFIG" >&2
  exit 2
fi

cd -- "$OPENPROVER_DIR"
uv run python -m openprover.math_research benchmark \
  --manifest "$MANIFEST" \
  --config "$CONFIG" \
  --output "$OUTPUT"
