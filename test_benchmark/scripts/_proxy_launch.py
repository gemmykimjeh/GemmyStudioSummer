"""Launch the LiteLLM proxy via ``python -m``, not the litellm.exe trampoline.

On Windows, Smart App Control / Application Control blocks the uv-generated
``litellm.exe`` launcher (it is an unsigned generated .exe), while ``python.exe``
is allowed. Invoking the proxy through the Python entry point sidesteps that. On
macOS/Linux this is simply a portable way to start the proxy.

    python scripts/_proxy_launch.py --config configs/litellm_gemini.yaml --port 4000

The real keys must already be in the environment (scripts/start_proxy.sh sources
configs/gemini_keys.env before calling this).
"""

from __future__ import annotations

import argparse
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description="Start the LiteLLM proxy (module entry).")
    ap.add_argument("--config", default="configs/litellm_gemini.yaml")
    ap.add_argument("--port", default="4000")
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()

    os.environ.setdefault("PYTHONUTF8", "1")

    try:
        # LiteLLM's proxy CLI is exposed as a runnable module; re-dispatch argv.
        from litellm.proxy.proxy_cli import run_server  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: could not import litellm proxy CLI ({exc}). "
              'Install it: pip install -e ".[gdpval]"  (or pip install "litellm[proxy]").',
              file=sys.stderr)
        return 1

    # run_server is a click command; invoke it with an explicit arg list so we do
    # not depend on sys.argv layout.
    return run_server(
        ["--config", args.config, "--port", str(args.port), "--host", args.host],
        standalone_mode=False,
    ) or 0


if __name__ == "__main__":
    raise SystemExit(main())
