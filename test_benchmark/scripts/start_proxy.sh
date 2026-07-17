#!/usr/bin/env bash
# Launch the LiteLLM key-rotation proxy for Gemini on :4000.
#
# Exports the rotation-pool keys from configs/gemini_keys.env so the litellm
# config's `os.environ/GEMINI_KEY_N` references resolve, then starts the proxy.
# Both the ACE brain (GEMINI_BASE_URL) and the grader (ANTHROPIC_BASE_URL) point
# at this one endpoint, so a 429 on any key transparently fails over to the next.
#
#   bash scripts/start_proxy.sh
#
# Leave it running in its own terminal; then launch a run in another:
#   ARM=fbl LIMIT=100 bash scripts/run_gdpval_gemini.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # -> test_benchmark
cd "$HERE"

if [ ! -f configs/gemini_keys.env ]; then
  echo "missing configs/gemini_keys.env (the key rotation pool)"; exit 1
fi
# export every GEMINI_KEY_* so litellm's os.environ/GEMINI_KEY_N resolves
set -a; source configs/gemini_keys.env; set +a

nkeys=$(grep -cE '^GEMINI_KEY_[0-9]+=' configs/gemini_keys.env)
echo ">>> starting LiteLLM rotation proxy on :4000 with $nkeys keys"
# Launch via python.exe, NOT the litellm.exe trampoline: Windows Application
# Control blocks the uv-generated .exe launchers, but python.exe is allowed.
PYTHONUTF8=1 exec ./.venv/Scripts/python.exe -c \
  "import sys; sys.argv=['litellm','--config','configs/litellm_gemini.yaml','--port','4000']; from litellm import run_server; run_server()"
