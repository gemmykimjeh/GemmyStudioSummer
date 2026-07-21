"""Detached launcher for the LiteLLM rotation proxy (avoids shell-quoting issues
with `python -c` and the uv .exe trampoline that Windows Application Control blocks).
Run from test_benchmark/ with GEMINI_KEY_* already exported."""
import sys

sys.argv = ["litellm", "--config", "configs/litellm_gemini.yaml", "--port", "4000"]
from litellm import run_server  # noqa: E402

run_server()
