#!/usr/bin/env bash
# gdpval A/B on the Gemini free tier (valid: 1M context, ACE's real operating
# point, NO local caps). Runs one arm; run twice (baseline + fbl) to compare.
#
#   ARM=baseline LIMIT=20 bash scripts/run_gdpval_gemini.sh
#   ARM=fbl      LIMIT=20 bash scripts/run_gdpval_gemini.sh
#
# Prereqs:
#   - Key-rotation proxy running on :4000 (fronts N Gemini keys, 429 failover):
#       bash scripts/start_proxy.sh
#     BOTH the grader (ANTHROPIC_BASE_URL) and the ACE brain (GEMINI_BASE_URL)
#     route through it, so a key running dry transparently fails over.
#   - GEMINI_API_KEY in the environment or .env (any value — the real keys live
#     in configs/gemini_keys.env, held by the proxy).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # -> test_benchmark
ROOT="$(dirname "$HERE")"                                  # -> GemmyStudioSummer
cd "$HERE"
ARM="${ARM:-baseline}"
LIMIT="${LIMIT:-20}"
MODEL="${MODEL:-gemini-3.1-flash-lite}"
RUN_ID="${RUN_ID:-gemini_${ARM}}"   # override to start a fresh run (no resume-skip)
# baseline = single-playbook ACE bridge (ace_gdpval, ReAct repo).
# fbl      = dual-playbook feedback loop (ace_dual_gdpval, ReAct_feedback_loop):
#            Thompson selector + auto_distill + verifier + two curators.
case "$ARM" in
  baseline) ACE_PATH="$ROOT/ReAct";               AGENT="ace_gdpval" ;;
  fbl)      ACE_PATH="$ROOT/ReAct_feedback_loop";  AGENT="ace_dual_gdpval" ;;
  *) echo "ARM must be 'baseline' or 'fbl'"; exit 2 ;;
esac
: "${GEMINI_API_KEY:?set GEMINI_API_KEY (or put it in .env and source it)}"
echo ">>> ARM=$ARM  AGENT=$AGENT  ACE_PATH=$ACE_PATH  MODEL=$MODEL  LIMIT=$LIMIT  RUN_ID=$RUN_ID"

# Route BOTH surfaces through the rotation proxy:
#   ANTHROPIC_BASE_URL -> grader (anthropic SDK)
#   GEMINI_BASE_URL    -> ACE brain (OpenAI client, utils.initialize_clients)
ACE_API_PROVIDER=gemini \
ACE_PATH="$ACE_PATH" \
ANTHROPIC_BASE_URL=http://localhost:4000 \
GEMINI_BASE_URL=http://localhost:4000/v1 \
HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 \
PYTHONUTF8=1 \
  "$HERE/.venv/Scripts/python.exe" -m harness.run \
    --agent "$AGENT" --benchmark gdpval --model "$MODEL" --split train \
    --limit "$LIMIT" --concurrency 1 --run-id "$RUN_ID" --resume
