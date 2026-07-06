#!/usr/bin/env bash
# Phase 0 Smoke Run — 48-task evaluation with all features ON
# Run from repo root: bash scripts/run_phase0_smoke.sh
# Usage: bash scripts/run_phase0_smoke.sh [run_label] [env_file] [concurrency] [--score-only]

set -euo pipefail

RUN_LABEL="${1:-phase0_all_features}"
ENV_FILE="${2:-.env.phase0}"
CONCURRENCY="${3:-48}"
SCORE_ONLY="${4:-}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MANIFEST="benchmarks/manifests/phase0_smoke_48.json"
RESULTS_DIR="results/${RUN_LABEL}"

if [[ ! -f "$MANIFEST" ]]; then
    echo "ERROR: Manifest not found: $MANIFEST" >&2
    exit 1
fi

# Load env file
if [[ -f "$ENV_FILE" ]]; then
    while IFS='=' read -r key value; do
        # Only export lines starting with an uppercase letter (same filter as PS1)
        if [[ "$key" =~ ^[A-Z] ]]; then
            export "$key"="$value"
            echo "  SET ${key}=${value}"
        fi
    done < "$ENV_FILE"
else
    echo "WARNING: No env file: $ENV_FILE (running with defaults)" >&2
fi

# Must have API key
if [[ -z "${GEMINI_API_KEY:-}" && -z "${GOOGLE_API_KEY:-}" ]]; then
    echo "ERROR: No API key. Set GEMINI_API_KEY or GOOGLE_API_KEY." >&2
    exit 1
fi

# Default bench root if not set
if [[ -z "${HARVEY_BENCH_ROOT:-}" ]]; then
    HARVEY_BENCH_ROOT="${HOME}/harvey-labs"
    export HARVEY_BENCH_ROOT
fi

echo ""
echo "=== Phase 0 Smoke: ${RUN_LABEL} ==="
echo "  Manifest:    $MANIFEST"
echo "  Results:     $RESULTS_DIR"
echo "  Concurrency: $CONCURRENCY"
echo "  Bench root:  $HARVEY_BENCH_ROOT"
echo ""

if [[ -z "$SCORE_ONLY" ]]; then
    echo "--- Running batch ---"
    t0=$(date +%s)
    python -m src.cli batch "$MANIFEST" -o "$RESULTS_DIR" -j "$CONCURRENCY"
    t1=$(date +%s)
    elapsed=$(( (t1 - t0) / 60 ))
    echo ""
    echo "Batch done in ${elapsed} minutes"
fi

echo ""
echo "--- Scoring ---"
python -m src.cli score "$RESULTS_DIR" --bench-root "$HARVEY_BENCH_ROOT" -j 20 --task-concurrency 5

echo ""
echo "--- Analysis ---"
python -m src.cli analyze "$RESULTS_DIR"

echo ""
echo "--- Lifecycle summary ---"
python -m src.cli summarize-lifecycle "$RESULTS_DIR"

echo ""
echo "Done: ${RUN_LABEL}"
