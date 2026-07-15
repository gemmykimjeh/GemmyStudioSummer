#!/usr/bin/env bash
# Run the full ablation ladder (a0 -> a1 -> a2 -> a3) back to back, then compare.
# Same task order across arms (train split, concurrency 1). Resumable per arm.
#
#   LIMIT=20 bash scripts/run_all_arms.sh
#
# Each arm writes runs/rgr_<mode>/... ; use test_benchmark/harness/compare.py or
# report.py to diff them. If a free-tier day runs out mid-arm, just re-run the
# same command tomorrow — every arm has --resume and its own playbook state.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIMIT="${LIMIT:-20}"

for MODE in a0 a1 a2 a3; do
  echo "==================== ARM $MODE ===================="
  MODE="$MODE" LIMIT="$LIMIT" bash "$HERE/run_rgr_gdpval.sh" || {
    echo "arm $MODE stopped (likely daily quota). Re-run this script tomorrow to resume."
    exit 0
  }
done
echo "All arms done. Compare with: python -m harness.compare runs/rgr_a0 runs/rgr_a3 (from test_benchmark/)"
