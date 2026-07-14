#!/usr/bin/env bash
# gdpval A/B on the Gemini free tier (valid: 1M context, ACE's real operating
# point, NO local caps). Runs one arm; run twice (baseline + fbl) to compare.
#
#   ARM=baseline LIMIT=20 bash scripts/run_gdpval_gemini.sh
#   ARM=fbl      LIMIT=20 bash scripts/run_gdpval_gemini.sh
#
# Prereqs:
#   - Gemini proxy running on :4000  (for the grader):
#       PYTHONUTF8=1 GEMINI_API_KEY=... .venv/Scripts/litellm.exe \
#         --config configs/litellm_gemini.yaml --port 4000
#   - GEMINI_API_KEY in the environment or .env (free key from AI Studio).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # -> test_benchmark
ROOT="$(dirname "$HERE")"                                  # -> GemmyStudioSummer
cd "$HERE"
ARM="${ARM:-baseline}"
LIMIT="${LIMIT:-20}"
MODEL="${MODEL:-gemini-3.1-flash-lite}"
case "$ARM" in
  baseline) ACE_PATH="$ROOT/ReAct" ;;
  fbl)      ACE_PATH="$ROOT/ReAct_feedback_loop" ;;
  *) echo "ARM must be 'baseline' or 'fbl'"; exit 2 ;;
esac
: "${GEMINI_API_KEY:?set GEMINI_API_KEY (or put it in .env and source it)}"
echo ">>> ARM=$ARM  ACE_PATH=$ACE_PATH  MODEL=$MODEL  LIMIT=$LIMIT"

ACE_API_PROVIDER=gemini \
ACE_PATH="$ACE_PATH" \
ANTHROPIC_BASE_URL=http://localhost:4000 \
HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 \
PYTHONUTF8=1 \
  "$HERE/.venv/Scripts/python.exe" -m harness.run \
    --agent ace_gdpval --benchmark gdpval --model "$MODEL" --split train \
    --limit "$LIMIT" --concurrency 1 --run-id "gemini_${ARM}" --resume
