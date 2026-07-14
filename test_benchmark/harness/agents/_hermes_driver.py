"""Driver executed *inside Hermes's own venv* to run one benchmark task.

This is OUR code (it lives in the harness repo) but is launched with Hermes's
interpreter and with ``hermes-agent`` on ``sys.path``, so it can import Hermes
without polluting our venv (Hermes pins older anthropic/pydantic). It does NOT
modify anything under C:/hermes.

Two modes (spec["mode"]):
  * "env"  (B, default): connect to the harness MCP bridge → register its tools
    as a custom Hermes toolset → Hermes solves the task with the BENCHMARK's tools.
  * "native" (A): no bridge — chdir into the benchmark's local workspace and run
    Hermes with its OWN toolset (spec["toolsets"], e.g. 'development'), so it uses
    its own file/terminal tools on that directory.

Usage (invoked by HermesAgent, not by hand):
    python _hermes_driver.py --spec <spec.json> --out <out.json>

spec.json (env):    {mode:"env", mcp_url, prompt, system_prompt, model, max_steps}
spec.json (native): {mode:"native", workspace, toolsets, prompt, system_prompt, ...}
out.json:  {ok, final_response, messages, tool_names, error?}
"""

from __future__ import annotations

import argparse
import json
import os
import traceback

# UTF-8 stdio on Windows — mirror Hermes's own entrypoints. No-op on POSIX.
try:
    import hermes_bootstrap  # noqa: F401
except Exception:  # pragma: no cover
    pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    with open(args.spec, encoding="utf-8") as fh:
        spec = json.load(fh)

    out: dict = {"ok": False}
    mode = spec.get("mode", "env")
    try:
        from run_agent import AIAgent

        if mode == "native":
            # A-mode: run in the benchmark's local workspace with Hermes's OWN
            # toolset — its file/terminal tools then act on that directory.
            ws = spec.get("workspace")
            if ws:
                os.chdir(ws)
            toolsets = [t.strip() for t in str(spec.get("toolsets", "development")).split(",")
                        if t.strip()]
            out["tool_names"] = None  # native tools, not MCP-bridged
        else:
            # B-mode: connect to the harness env exposed over MCP, register its
            # tools, and expose exactly those to the agent as a custom toolset.
            from tools.mcp_tool import register_mcp_servers
            from toolsets import create_custom_toolset
            tool_names = register_mcp_servers({"bench": {"url": spec["mcp_url"]}})
            out["tool_names"] = tool_names
            if not tool_names:
                out["error"] = "no MCP tools registered (bridge unreachable?)"
                _dump(args.out, out)
                return 1
            create_custom_toolset(
                "bench_bridge", "Benchmark environment tools (via MCP)",
                tools=tool_names)
            toolsets = ["bench_bridge"]

        # Run one conversation on Hermes's own Claude account (HERMES_HOME).
        verbose = bool(spec.get("verbose"))
        agent_kwargs = dict(
            enabled_toolsets=toolsets,
            max_iterations=int(spec.get("max_steps", 50)),
            quiet_mode=not verbose,
            verbose_logging=verbose,
            save_trajectories=False,
        )
        if spec.get("model"):
            agent_kwargs["model"] = spec["model"]
        if spec.get("provider"):
            agent_kwargs["provider"] = spec["provider"]
        if spec.get("system_prompt"):
            agent_kwargs["ephemeral_system_prompt"] = spec["system_prompt"]

        agent = AIAgent(**agent_kwargs)
        result = agent.run_conversation(
            spec["prompt"], system_message=spec.get("system_prompt"))

        out["ok"] = True
        out["final_response"] = result.get("final_response")
        out["messages"] = result.get("messages", [])
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["traceback"] = traceback.format_exc()

    _dump(args.out, out)
    return 0 if out.get("ok") else 1


def _dump(path: str, obj: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, default=str)


if __name__ == "__main__":
    raise SystemExit(main())
