#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Run one RGR ablation arm on GDPval, Gemini free-tier, multi-key rotation.
#
#   MODE=a0 LIMIT=20 bash scripts/run_rgr_gdpval.sh
#   MODE=a1 LIMIT=20 bash scripts/run_rgr_gdpval.sh
#   MODE=a2 LIMIT=20 bash scripts/run_rgr_gdpval.sh
#   MODE=a3 LIMIT=20 bash scripts/run_rgr_gdpval.sh
#
# Multiple Gemini keys (recommended: 3-4) so the run survives RPM limits:
#   export GEMINI_API_KEYS="key1,key2,key3,key4"
# The rotating client parks a key on 429 and moves to the next; when all are
# cooling down it sleeps. If every key hits the DAILY cap the run stops cleanly
# and `--resume` picks it up the next day with the playbook/counters intact.
#
# Prereqs:
#   1) LiteLLM proxy on :4000 for the GRADER (Anthropic SDK -> Gemini):
#        PYTHONUTF8=1 GEMINI_API_KEY=<one_key> \
#          litellm --config configs/litellm_gemini.yaml --port 4000
#      (the proxy needs only ONE key; the generator/reflector rotate all keys)
#   2) GEMINI_API_KEYS set in the environment (or a .env you source).
# ---------------------------------------------------------------------------
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # -> feedback_loop_gdpval
ROOT="$(dirname "$HERE")"                                  # -> GemmyStudioSummer
TB="$ROOT/test_benchmark"

MODE="${MODE:-a3}"
LIMIT="${LIMIT:-20}"
MODEL="${MODEL:-gemini-3.1-flash-lite}"

case "$MODE" in a0|a1|a2|a3) ;; *) echo "MODE must be a0|a1|a2|a3"; exit 2 ;; esac
: "${GEMINI_API_KEYS:=${GEMINI_API_KEY:?set GEMINI_API_KEYS='k1,k2,k3' (or GEMINI_API_KEY)}}"
export GEMINI_API_KEYS

echo ">>> MODE=$MODE  MODEL=$MODEL  LIMIT=$LIMIT  keys=$(echo "$GEMINI_API_KEYS" | tr ',' '\n' | grep -c .)"

cd "$TB"
# pick python: prefer test_benchmark/.venv, else the active one
PY="python"
[ -x "$TB/.venv/bin/python" ]     && PY="$TB/.venv/bin/python"
[ -x "$TB/.venv/Scripts/python.exe" ] && PY="$TB/.venv/Scripts/python.exe"

RGR_MODE="$MODE" \
RGR_AGENTS_DIR="$HERE/agents" \
ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-http://localhost:4000}" \
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-sk-proxy}" \
HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-0}" \
PYTHONUTF8=1 \
  "$PY" -m harness.run \
    --agent rgr_gdpval --benchmark gdpval --model "$MODEL" --split train \
    --limit "$LIMIT" --concurrency 1 --run-id "rgr_${MODE}" --resume
