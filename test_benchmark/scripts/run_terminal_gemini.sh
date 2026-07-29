#!/usr/bin/env bash
# Terminal-Bench 2.0 A/B on the Gemini free tier, through the key-rotation proxy.
# The ACE-on-NexAU0 arms (ace_terminal / ace_dual_terminal) run their shell loop
# on Gemini instead of Claude: BOTH surfaces route through the LiteLLM :4000 proxy
# that fronts the 6 Gemini keys and fails over ONLY on RPD (daily) 429s.
#
#   ARM=baseline LIMIT=20 bash scripts/run_terminal_gemini.sh
#   ARM=fbl      LIMIT=20 bash scripts/run_terminal_gemini.sh
#
# Two surfaces, one proxy (identical wiring to run_gdpval_gemini.sh):
#   * shell loop (NexAU0 seed) = anthropic SDK   -> ANTHROPIC_BASE_URL=:4000
#   * ACE brain (Reflector/Curator) = OpenAI cli -> GEMINI_BASE_URL=:4000/v1
#                                                   + ACE_API_PROVIDER=gemini
#
# Prereqs:
#   - The rotation proxy running on :4000 (in its own terminal):
#       bash scripts/start_proxy.sh
#   - Docker reachable (Terminal-Bench 2.0 runs each task in its container). Point
#     at a remote daemon with ENDPOINT=tcp://HOST:2375 if not local.
#   - external/terminal-bench-2 present (git clone the 2.0 tasks; see
#     harness/benchmarks/terminal_bench.py).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # -> test_benchmark
ROOT="$(dirname "$HERE")"                                  # -> GemmyStudioSummer
cd "$HERE"

ARM="${ARM:-baseline}"
LIMIT="${LIMIT:-20}"
MODEL="${MODEL:-gemini-3.1-flash-lite}"
RUN_ID="${RUN_ID:-tb2_gemini_${ARM}}"   # override for a fresh run (no resume-skip)
PROXY="${PROXY:-http://localhost:4000}"

# baseline = single-playbook ACE bridge on the NexAU0 seed (ace_terminal, ReAct).
# fbl      = feedback-loop ACE (single playbook + immutable rulebook) on the same
#            seed (ace_dual_terminal, ReAct_feedback_loop).
case "$ARM" in
  baseline) ACE_PATH="$ROOT/ReAct";               AGENT="ace_terminal" ;;
  fbl)      ACE_PATH="$ROOT/ReAct_feedback_loop";  AGENT="ace_dual_terminal" ;;
  *) echo "ARM must be 'baseline' or 'fbl'"; exit 2 ;;
esac

# The real Gemini keys live in the proxy (configs/gemini_keys.env). The client
# keys below are just non-empty placeholders the SDKs require; the proxy ignores
# them and injects the real rotation-pool key server-side.
export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-proxy-local}"   # shell-loop SDK
export GEMINI_API_KEY="${GEMINI_API_KEY:-proxy-local}"         # ACE brain OpenAI cli

# Fail fast if the proxy is not up (both surfaces depend on it).
if ! curl -sf -o /dev/null "$PROXY/health" 2>/dev/null \
   && ! curl -sf -o /dev/null "$PROXY/v1/models" 2>/dev/null; then
  echo ">>> WARNING: rotation proxy not reachable at $PROXY — start it first:"
  echo ">>>          bash scripts/start_proxy.sh"
fi

mkdir -p "$HERE/logs" "$HERE/playbooks"
LOG="${LOG:-$HERE/logs/${RUN_ID}_live.log}"
echo ">>> ARM=$ARM  AGENT=$AGENT  ACE_PATH=$ACE_PATH  MODEL=$MODEL  LIMIT=$LIMIT  RUN_ID=$RUN_ID"
echo ">>> shell loop + ACE brain both route through $PROXY (6-key Gemini, RPD failover)"
echo ">>> log: $LOG"

# ENDPOINT (optional) forwards to the benchmark's --env-endpoint for remote Docker.
# TASKS   (optional) = comma-separated task-id subset (a stratified batch); when
#                     set it selects exactly those tasks and LIMIT is ignored.
EXTRA=()
[ -n "${ENDPOINT:-}" ] && EXTRA+=(--env-endpoint "$ENDPOINT")
if [ -n "${TASKS:-}" ]; then
  EXTRA+=(--tasks "$TASKS")
  echo ">>> TASKS subset (${TASKS//,/, }) — LIMIT ignored"
else
  EXTRA+=(--limit "$LIMIT")
fi

# Route BOTH surfaces through the rotation proxy, exactly like run_gdpval_gemini.sh:
#   ANTHROPIC_BASE_URL -> shell loop (anthropic SDK, the NexAU0 seed)
#   GEMINI_BASE_URL    -> ACE brain (OpenAI client, utils.initialize_clients)
ACE_API_PROVIDER=gemini \
ACE_PATH="$ACE_PATH" \
ANTHROPIC_BASE_URL="$PROXY" \
GEMINI_BASE_URL="$PROXY/v1" \
PYTHONUTF8=1 PYTHONUNBUFFERED=1 \
  "$HERE/.venv/Scripts/python.exe" -m harness.run \
    --agent "$AGENT" --benchmark terminal_bench --model "$MODEL" \
    --concurrency 1 --run-id "$RUN_ID" --resume \
    "${EXTRA[@]}" 2>&1 | tee -a "$LOG"
