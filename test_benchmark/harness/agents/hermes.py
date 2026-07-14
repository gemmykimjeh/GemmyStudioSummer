"""HermesAgent — real integration with the NousResearch Hermes agent.

Hermes (at ``C:/hermes/hermes-agent``) is a separate project with its own venv
and exact-pinned deps (anthropic/pydantic older than ours), so we drive it as a
**subprocess** using *its* interpreter — never importing it into our venv, never
modifying anything under C:/hermes.

Tool boundary: Hermes solves tasks with tools registered in its own registry.
To make it act on a benchmark's ``Env`` we expose that env over an in-process
**MCP** server (:class:`EnvMCPBridge`); a small driver (``_hermes_driver.py``),
run with Hermes's venv, registers those MCP tools as a custom Hermes toolset and
runs one conversation. Hermes's tool calls route back over MCP to ``env`` in our
process, which the benchmark then scores. Hermes authenticates with its own
Claude account (``HERMES_HOME``), so its reasoning is billed there, not to our key.

Parameterization (req 2): the benchmark's domain policy is injected as Hermes's
``ephemeral_system_prompt`` (the task/context layer); Hermes's stable prompt +
memory/skills/notepads/DB come from its ``HERMES_HOME`` workspace, which is a
constructor arg (``hermes_home``) so different memory/skill workspaces can be
A/B'd. Paths are never hardcoded — resolved from args then ``HERMES_*`` env vars.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from harness.agent import Agent
from harness.agents._mcp_bridge import EnvMCPBridge
from harness.agents._react import messages_to_trajectory
from harness.benchmark import Env
from harness.registry import register_agent
from harness.schema import Step, Task, Trajectory

_DRIVER = Path(__file__).resolve().parent / "_hermes_driver.py"


@register_agent("hermes")
class HermesAgent(Agent):
    """Drives the real Hermes agent as a subprocess, bridged via MCP."""

    def __init__(
        self,
        hermes_path: str | None = None,
        hermes_home: str | None = None,       # memory/skills/notepads workspace (A/B)
        hermes_userprofile: str | None = None,
        python: str | None = None,
        # Hermes doesn't resolve its own config model into the request client
        # for this programmatic path, so set one explicitly (matches Hermes's
        # documented default install). Override on the CLI with --model.
        model: str | None = "claude-opus-4-8",
        provider: str | None = "anthropic",  # native client; the default Hermes install is Anthropic-direct
        max_steps: int = 50,
        timeout: float = 900.0,
        use_own_account: bool = True,
        native_tools: bool = False,
        native_toolsets: str = "development",   # Hermes's own coding toolset for A-mode
    ) -> None:
        self.hermes_path = Path(
            hermes_path or os.environ.get("HERMES_PATH")
            or r"C:\hermes\hermes-agent")
        self.hermes_home = Path(
            hermes_home or os.environ.get("HERMES_HOME") or r"C:\hermes\.hermes")
        self.hermes_userprofile = Path(
            hermes_userprofile or os.environ.get("HERMES_USERPROFILE")
            or r"C:\hermes\.home")
        self.python = Path(
            python or (self.hermes_path / ".venv" / "Scripts" / "python.exe"))
        self.model = model
        self.provider = provider  # e.g. "anthropic" to force the native client
        self.max_steps = max_steps
        self.timeout = timeout
        # By default Hermes uses its OWN Claude account (HERMES_HOME/OAuth) and
        # our keys are stripped. Set False to let it borrow our ANTHROPIC_API_KEY
        # (e.g. when Hermes's own account isn't configured).
        self.use_own_account = use_own_account
        # A/B mode (mirrors claude_agent_sdk):
        #   native_tools=False (B, default): bridge the benchmark's Env over MCP;
        #     Hermes solves the task with the BENCHMARK's tools. Works on any bench.
        #   native_tools=True  (A): no MCP bridge — Hermes uses its OWN toolset
        #     (``native_toolsets``, e.g. 'development': files/terminal/search) in
        #     the benchmark's local env.workspace(). Needs an A-capable benchmark;
        #     B-only benches (no workspace) raise a clear error.
        self.native_tools = native_tools
        self.native_toolsets = native_toolsets

    def _preflight(self) -> None:
        if not (self.hermes_path / "run_agent.py").exists():
            raise RuntimeError(
                f"Hermes not found at {self.hermes_path} (no run_agent.py). "
                "Set HERMES_PATH or pass hermes_path=.")
        if not self.python.exists():
            raise RuntimeError(
                f"Hermes venv python not found at {self.python}. "
                "Point python= at Hermes's interpreter.")

    def run(self, task: Task, env: Env) -> Trajectory:
        self._preflight()
        start = time.perf_counter()
        traj = Trajectory()
        traj.add(Step(type="user_message", text=env.observation()))

        common = dict(
            prompt=env.observation(),
            system_prompt=env.instructions(),
            model=self.model,
            provider=self.provider,
            max_steps=self.max_steps,
            verbose=bool(os.environ.get("HERMES_VERBOSE")),
        )

        if self.native_tools:
            # A-mode: Hermes uses its OWN toolset in the benchmark's local workspace.
            ws = env.workspace()
            if ws is None:
                raise RuntimeError(
                    "native_tools (A-mode) needs a benchmark that exposes a local "
                    f"workspace(), but {type(env).__name__} is B-mode ONLY — its "
                    "environment is a container/VM/tool-API, not a local directory. "
                    "A-capable today: swe_bench. Run without --native-tools to use "
                    "the MCP tool-bridge (B) mode.")
            spec = {**common, "mode": "native", "workspace": str(ws),
                    "toolsets": self.native_toolsets}
            result = self._invoke_driver(spec)
        else:
            # B-mode: expose the benchmark Env over MCP; Hermes uses those tools.
            bridge = EnvMCPBridge(env)
            url = bridge.start()
            try:
                spec = {**common, "mode": "env", "mcp_url": url}
                result = self._invoke_driver(spec, bridge=bridge)
            finally:
                bridge.stop()

        if not result.get("ok"):
            raise RuntimeError(
                "Hermes run failed: "
                + (result.get("error") or "unknown")
                + (f"\n{result.get('traceback','')}" if result.get("traceback")
                   else ""))

        # Generic ReAct-transcript -> Trajectory (shared with other agents).
        messages_to_trajectory(result.get("messages", []), traj)
        traj.final_output = result.get("final_response")
        traj.final_state = env.snapshot()
        traj.wall_time = time.perf_counter() - start
        # Cost is on Hermes's own Claude account, not measurable from here.
        traj.cost = 0.0
        return traj

    def _invoke_driver(self, spec: dict, bridge=None) -> dict:
        """Run the Hermes driver subprocess (its venv) on ``spec``; return its result."""
        with tempfile.TemporaryDirectory(prefix="hermes_task_") as td:
            spec_path = Path(td) / "spec.json"
            out_path = Path(td) / "out.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")

            proc_env = dict(os.environ)
            proc_env["HERMES_HOME"] = str(self.hermes_home)
            proc_env["USERPROFILE"] = str(self.hermes_userprofile)
            # Hermes authenticates with its OWN account (HERMES_HOME / its OAuth);
            # drop our keys so its reasoning is never billed to us — unless
            # use_own_account=False (Hermes borrows our key).
            if self.use_own_account:
                for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                          "ANTHROPIC_AUTH_TOKEN"):
                    proc_env.pop(k, None)
            # Prepend Hermes to PYTHONPATH so the driver can import it.
            proc_env["PYTHONPATH"] = (
                str(self.hermes_path) + os.pathsep + proc_env.get("PYTHONPATH", ""))
            proc_env["PYTHONUTF8"] = "1"

            completed = subprocess.run(
                [str(self.python), str(_DRIVER),
                 "--spec", str(spec_path), "--out", str(out_path)],
                cwd=str(self.hermes_path), env=proc_env,
                capture_output=True, text=True, timeout=self.timeout, shell=False,
            )
            result = self._read_out(out_path, completed)

            debug_path = os.environ.get("HERMES_DEBUG")
            if debug_path:
                Path(debug_path).write_text(json.dumps({
                    "mode": spec.get("mode"),
                    "ok": result.get("ok"),
                    "error": result.get("error"),
                    "tool_names": result.get("tool_names"),
                    "bridge_calls": getattr(bridge, "calls", None),
                    "final_response": result.get("final_response"),
                    "n_messages": len(result.get("messages", []) or []),
                    "stderr_tail": (completed.stderr or "").splitlines()[-80:],
                }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                Path(debug_path + ".stderr.txt").write_text(
                    completed.stderr or "", encoding="utf-8")
            return result

    @staticmethod
    def _read_out(out_path: Path, completed: subprocess.CompletedProcess) -> dict:
        if out_path.exists():
            try:
                return json.loads(out_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        # Driver produced no parseable output — surface its stderr tail.
        tail = (completed.stderr or completed.stdout or "").strip().splitlines()
        return {"ok": False,
                "error": f"driver exit {completed.returncode}; "
                         + (" / ".join(tail[-5:]) if tail else "no output")}
