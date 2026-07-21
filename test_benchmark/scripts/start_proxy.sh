#!/usr/bin/env bash
# Start the LiteLLM proxy on :4000 for the GDPval harness (multi-key Gemini
# fan-out). Leave it running in its own terminal.
#
#   bash scripts/start_proxy.sh
#   # then, in another terminal, health-check it:
#   curl -s -o /dev/null -w "%{http_code}\n" http://localhost:4000/health   # 200
#
# Point both surfaces at it before running the benchmark:
#   export GEMINI_BASE_URL=http://localhost:4000/v1   # the agent brain
#   export ANTHROPIC_BASE_URL=http://localhost:4000   # the grader
#   export GEMINI_API_KEY=sk-proxy-rotation           # placeholder; proxy holds real keys
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # -> test_benchmark
cd "$HERE"

PORT="${PORT:-4000}"
CONFIG="${CONFIG:-configs/litellm_gemini.yaml}"
KEYS_FILE="${KEYS_FILE:-configs/gemini_keys.env}"

# Load the real keys (gitignored). Fall back to the example only to warn.
if [[ -f "$KEYS_FILE" ]]; then
  set -a; source "$KEYS_FILE"; set +a
else
  echo "!! $KEYS_FILE not found. Copy configs/gemini_keys.env.example -> $KEYS_FILE"
  echo "   and fill in your Gemini key(s). Falling back to GEMINI_API_KEY if set."
fi

# GEMINI_KEY_1 is required; _2/_3 fall back to _1 so a single key still works.
: "${GEMINI_KEY_1:=${GEMINI_API_KEY:-}}"
if [[ -z "${GEMINI_KEY_1:-}" ]]; then
  echo "ERROR: no GEMINI_KEY_1 (or GEMINI_API_KEY). Set at least one key."; exit 2
fi
export GEMINI_KEY_1
export GEMINI_KEY_2="${GEMINI_KEY_2:-$GEMINI_KEY_1}"
export GEMINI_KEY_3="${GEMINI_KEY_3:-$GEMINI_KEY_1}"

echo ">>> starting LiteLLM proxy on :$PORT  config=$CONFIG"

# Prefer python -m litellm over the litellm.exe trampoline: on Windows,
# Application Control blocks uv-generated .exe launchers, but python.exe is
# allowed. On macOS/Linux either works; the module form is portable.
PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
  if [[ -x ".venv/Scripts/python.exe" ]]; then PY=".venv/Scripts/python.exe"    # Windows venv
  elif [[ -x ".venv/bin/python" ]]; then      PY=".venv/bin/python"            # unix venv
  else                                         PY="python"; fi
fi

PYTHONUTF8=1 exec "$PY" scripts/_proxy_launch.py --config "$CONFIG" --port "$PORT"
