#!/usr/bin/env bash
# Run gdpval on the LOCAL LM Studio model (baseline ACE = ReAct, not the FBL repo).
# Prereqs: LM Studio server On (:1234, model id "qwen3-8b") + proxy On (:4000).
#   LIMIT=1 bash scripts/run_gdpval_local.sh
#   LIMIT=all RUN_ID=local_gdpval_full bash scripts/run_gdpval_local.sh
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"
LIMIT="${LIMIT:-1}"
RUN_ID="${RUN_ID:-local_gdpval_$(date +%s 2>/dev/null || echo run)}"
LIMIT_ARG=(--limit "$LIMIT")
[ "$LIMIT" = "all" ] && LIMIT_ARG=()   # omit --limit -> full set

ACE_API_PROVIDER=local \
ANTHROPIC_BASE_URL=http://localhost:4000 \
LOCAL_LLM_BASE_URL=http://localhost:1234/v1 \
LOCAL_LLM_API_KEY=lm-studio \
HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 \
PYTHONUTF8=1 \
  "$HERE/.venv/Scripts/python.exe" -m harness.run \
    --agent ace_gdpval --benchmark gdpval --model qwen3-8b --split train \
    "${LIMIT_ARG[@]}" --concurrency 1 --run-id "$RUN_ID" --resume
