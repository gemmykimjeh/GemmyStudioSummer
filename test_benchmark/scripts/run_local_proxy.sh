#!/usr/bin/env bash
# LiteLLM local translator (Anthropic Messages -> LM Studio OpenAI-compat).
# Leave this running in its own terminal while you run the harness.
#
#   bash scripts/run_local_proxy.sh              # default port 4000
#   PORT=4001 bash scripts/run_local_proxy.sh    # override port
#
# Prereq: LM Studio server On at http://localhost:1234, model loaded.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # -> test_benchmark
PORT="${PORT:-4000}"
# PYTHONUTF8=1 is REQUIRED: litellm prints a unicode banner that crashes on the
# cp949 (Korean Windows) console otherwise.
PYTHONUTF8=1 exec "$HERE/.venv/Scripts/litellm.exe" \
  --config "$HERE/configs/litellm_local.yaml" --port "$PORT"
