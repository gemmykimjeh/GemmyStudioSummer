"""terminal_bench — Terminal-Bench 2.0 shell tasks (real).

This runs the **Terminal-Bench 2.0** dataset (the harbor-era task set), NOT the
older terminal-bench-core / original tasks. Tasks live at
``external/terminal-bench-2`` (or ``$TERMINAL_BENCH_TASKS`` / ``tasks_dir``):
  git clone https://github.com/laude-institute/terminal-bench-2 external/terminal-bench-2

(a) License:        Apache-2.0 (laude-institute/terminal-bench-2, Terminal-Bench 2.0).
                    Prebuilt task images bundle third-party software under their own terms.
(b) Infrastructure: Docker (Docker Desktop / WSL2 on Windows). Each 2.0 task ships
                    a **prebuilt image** (``[environment].docker_image``), so setup
                    pulls + runs it (no build). Use the local daemon or point
                    ``--env-endpoint`` at a remote Docker (sets ``DOCKER_HOST``).
                    No Docker reachable -> precise precheck error.
(c) Scoring:        the OFFICIAL 2.0 verifier — copy the task's ``tests/`` into the
                    container and run its ``tests/test.sh`` exactly as harbor does;
                    it runs pytest and writes ``/logs/verifier/reward.txt`` (1/0).
                    We report that reward as success/score. No substitution.
(d) Official:       harbor https://github.com/laude-institute/harbor ·
                    tasks https://github.com/laude-institute/terminal-bench-2 ·
                    dataset HF harborframework/terminal-bench-2.0

Task format (each dir): ``task.toml`` (docker_image, timeouts, resources),
``instruction.md`` (the prompt), ``environment/`` (Dockerfile, unused here since
the image is prebuilt), ``tests/test.sh`` + ``tests/test_outputs.py`` (verifier),
``solution/``. The Env exposes one ``bash`` tool (``docker exec`` into the task
image) so the agent under test solves the task with shell commands.

Note: we run the agent phase with networking on (most 2.0 tasks set
``allow_internet = true``; the verifier itself needs the network to fetch uv +
pytest). ``allow_internet = false`` isolation is recorded in metadata but not
enforced in v1.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
import uuid
from pathlib import Path

from harness.benchmark import Benchmark, Env
from harness.registry import register_benchmark
from harness.schema import Result, Task, ToolResult, ToolSpec, Trajectory

_DEFAULT_TASKS_DIR = (
    Path(__file__).resolve().parents[2] / "external" / "terminal-bench-2"
)
_MAX_OUTPUT = 4000  # cap tool output returned to the agent
_CLONE_HINT = (
    "Terminal-Bench 2.0 tasks not found at {d}. Clone them:\n"
    "  git clone https://github.com/laude-institute/terminal-bench-2 "
    "external/terminal-bench-2\n"
    "(or set TERMINAL_BENCH_TASKS / pass tasks_dir).")


# ----------------------------------------------------------- docker helpers
def _docker(args: list[str], endpoint: str | None, timeout: float | None = None
            ) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if endpoint:
        env["DOCKER_HOST"] = endpoint
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          env=env, timeout=timeout, shell=False)


def _docker_ok(endpoint: str | None) -> bool:
    try:
        return _docker(["version"], endpoint, timeout=30).returncode == 0
    except Exception:  # noqa: BLE001
        return False


class TerminalBenchEnv(Env):
    """A running 2.0 task container; the agent acts via a single `bash` tool."""

    def __init__(self, task: Task, image: str, container: str,
                 endpoint: str | None) -> None:
        self.task = task
        self.image = image
        self.container = container
        self.endpoint = endpoint
        self.n_commands = 0

    def observation(self) -> str:
        return self.task.prompt

    def instructions(self) -> str:
        return (
            "You are working inside a Linux container via the `bash` tool. "
            "Accomplish the task by running shell commands — inspect the system, "
            "make the required changes, and write any required output files. Each "
            "`bash` call runs a command and returns its combined stdout/stderr. "
            "When the task is complete, stop."
        )

    def tools(self) -> list[ToolSpec]:
        return [ToolSpec(
            name="bash",
            description="Run a shell command in the task's Linux container and "
                        "return its combined stdout/stderr.",
            parameters={"type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"]},
        )]

    def call_tool(self, name: str, arguments: dict) -> ToolResult:
        if name != "bash":
            return ToolResult(output=f"unknown tool {name!r}", is_error=True)
        cmd = (arguments or {}).get("command", "")
        self.n_commands += 1
        try:
            p = _docker(["exec", self.container, "bash", "-lc", cmd],
                        self.endpoint, timeout=120)
        except subprocess.TimeoutExpired:
            return ToolResult(output="[command timed out after 120s]", is_error=True)
        out = (p.stdout or "") + (p.stderr or "")
        if len(out) > _MAX_OUTPUT:
            out = out[:_MAX_OUTPUT] + "\n...[truncated]"
        if p.returncode != 0:
            out = f"[exit {p.returncode}]\n{out}"
        return ToolResult(output=out or "[no output]", is_error=p.returncode != 0)

    def snapshot(self) -> dict:
        return {"container": self.container, "commands": self.n_commands}


@register_benchmark("terminal_bench")
class TerminalBench(Benchmark):
    """Terminal-Bench 2.0 over local or remote (``--env-endpoint``) Docker."""

    def __init__(
        self,
        env_endpoint: str | None = None,
        tasks_dir: str | None = None,
        task_ids: str | tuple[str, ...] | None = None,
        pull_timeout: float = 1800.0,
    ) -> None:
        self.env_endpoint = env_endpoint or os.environ.get("TERMINAL_BENCH_ENDPOINT")
        self.tasks_dir = Path(
            tasks_dir or os.environ.get("TERMINAL_BENCH_TASKS") or _DEFAULT_TASKS_DIR)
        if isinstance(task_ids, str):
            task_ids = tuple(t.strip() for t in task_ids.split(",") if t.strip())
        self.task_ids = tuple(task_ids) if task_ids else None
        self.pull_timeout = pull_timeout

    # -- tasks ------------------------------------------------------------
    def load_tasks(self, limit: int | None = None) -> list[Task]:
        if not self.tasks_dir.is_dir():
            raise RuntimeError(_CLONE_HINT.format(d=self.tasks_dir))
        tasks = []
        for d in sorted(p for p in self.tasks_dir.iterdir() if p.is_dir()):
            if self.task_ids and d.name not in self.task_ids:
                continue
            toml_f, instr_f = d / "task.toml", d / "instruction.md"
            if not (toml_f.exists() and instr_f.exists()
                    and (d / "tests" / "test.sh").exists()):
                continue
            cfg = tomllib.loads(toml_f.read_text(encoding="utf-8"))
            environ = cfg.get("environment", {})
            image = environ.get("docker_image")
            if not image:  # 2.0 tasks all ship a prebuilt image; skip if absent
                continue
            tasks.append(Task(
                id=d.name, benchmark="terminal_bench",
                prompt=instr_f.read_text(encoding="utf-8").strip(),
                metadata={
                    "task_dir": str(d),
                    "docker_image": image,
                    "allow_internet": bool(environ.get("allow_internet", True)),
                    "cpus": environ.get("cpus"),
                    "memory_mb": environ.get("memory_mb"),
                    "verifier_timeout_sec": float(
                        cfg.get("verifier", {}).get("timeout_sec", 900)),
                    "difficulty": cfg.get("metadata", {}).get("difficulty"),
                },
            ))
        return tasks[:limit] if limit is not None else tasks

    # -- setup ------------------------------------------------------------
    def _precheck(self) -> None:
        if not _docker_ok(self.env_endpoint):
            where = (f"the Docker endpoint {self.env_endpoint!r}"
                     if self.env_endpoint else "the local Docker daemon")
            raise RuntimeError(
                f"terminal_bench (2.0) needs Docker, but {where} is not reachable.\n"
                "  - Local: install/start Docker Desktop (WSL2 backend on Windows) "
                "and ensure `docker version` works.\n"
                "  - Remote/prepared: pass --env-endpoint tcp://HOST:2375 "
                "(or DOCKER_HOST) pointing at a running Docker daemon.")

    def setup(self, task: Task) -> Env:
        self._precheck()
        image = task.metadata["docker_image"]
        uid = uuid.uuid4().hex[:8]
        safe = re.sub(r"[^a-z0-9]+", "-", task.id.lower())[:40]
        container = f"tb2-{safe}-{uid}"

        args = ["run", "-d", "--name", container, "--entrypoint", "sleep"]
        if task.metadata.get("cpus"):
            args += ["--cpus", str(task.metadata["cpus"])]
        if task.metadata.get("memory_mb"):
            args += ["--memory", f"{int(task.metadata['memory_mb'])}m"]
        args += [image, "infinity"]  # keep the prebuilt image alive; pulls if absent
        run = _docker(args, self.env_endpoint, timeout=self.pull_timeout)
        if run.returncode != 0:
            raise RuntimeError(
                f"docker run/pull failed for task {task.id} (image {image}):\n"
                + (run.stderr or run.stdout)[-1500:])
        return TerminalBenchEnv(task, image, container, self.env_endpoint)

    def teardown(self, env: Env) -> None:
        if isinstance(env, TerminalBenchEnv):
            # leave the shared prebuilt image cached; only remove the container
            _docker(["rm", "-f", env.container], env.endpoint)

    # -- scoring (official 2.0 verifier) ---------------------------------
    def score(self, task: Task, trajectory: Trajectory, env: Env) -> Result:
        assert isinstance(env, TerminalBenchEnv)
        base = dict(task_id=task.id, benchmark=self.name, agent="",
                    cost=trajectory.cost, wall_time=trajectory.wall_time)
        task_dir = Path(task.metadata["task_dir"])
        ep = env.endpoint

        # Reproduce harbor's verifier contract: /tests + /logs/verifier, then test.sh.
        _docker(["exec", env.container, "mkdir", "-p", "/tests", "/logs/verifier"],
                ep, timeout=60)
        cp = _docker(["cp", f"{task_dir / 'tests'}/.", f"{env.container}:/tests/"],
                     ep, timeout=120)
        if cp.returncode != 0:
            return Result(**base, success=False, score=0.0,
                          error="failed to copy tests into container: "
                                + (cp.stderr or cp.stdout)[-500:],
                          metrics={"num_commands": env.n_commands})
        # A Windows checkout (git autocrlf) leaves CRLF in the shell scripts, which
        # breaks bash in the Linux container ($'\r': command not found). Normalize.
        _docker(["exec", env.container, "sh", "-c",
                 r"find /tests -name '*.sh' -exec sed -i 's/\r$//' {} + 2>/dev/null || true"],
                ep, timeout=60)
        timeout = task.metadata.get("verifier_timeout_sec", 900) + 120
        try:
            tp = _docker(["exec", env.container, "bash", "/tests/test.sh"],
                         ep, timeout=timeout)
        except subprocess.TimeoutExpired:
            return Result(**base, success=False, score=0.0,
                          error=f"verifier timed out after {timeout}s",
                          metrics={"num_commands": env.n_commands})

        reward = self._read_reward(env, ep)
        tests = self._read_ctrf(env, ep)
        if reward is None:
            out = ((tp.stdout or "") + (tp.stderr or ""))[-600:]
            return Result(**base, success=False, score=0.0,
                          error="verifier did not write /logs/verifier/reward.txt "
                                f"(exit {tp.returncode}). tail:\n{out}",
                          metrics={"num_commands": env.n_commands})
        return Result(
            **base, success=reward >= 1.0, score=float(reward),
            metrics={"reward": reward, "num_commands": env.n_commands,
                     "difficulty": task.metadata.get("difficulty"),
                     **tests},
        )

    def _read_reward(self, env: TerminalBenchEnv, ep: str | None) -> float | None:
        r = _docker(["exec", env.container, "cat", "/logs/verifier/reward.txt"],
                    ep, timeout=60)
        if r.returncode != 0:
            return None
        try:
            return float((r.stdout or "").strip())
        except ValueError:
            return None

    def _read_ctrf(self, env: TerminalBenchEnv, ep: str | None) -> dict:
        """Best-effort per-test counts from the CTRF json the verifier emits."""
        r = _docker(["exec", env.container, "cat", "/logs/verifier/ctrf.json"],
                    ep, timeout=60)
        if r.returncode != 0:
            return {}
        try:
            summary = (json.loads(r.stdout).get("results", {}) or {}).get("summary", {})
        except Exception:  # noqa: BLE001
            return {}
        return {"tests_total": summary.get("tests"),
                "tests_passed": summary.get("passed"),
                "tests_failed": summary.get("failed")}
