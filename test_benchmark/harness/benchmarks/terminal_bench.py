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
import time
import tomllib
import uuid
from pathlib import Path

from harness.benchmark import Benchmark, Env
from harness.registry import register_benchmark
from harness.schema import Result, Task, ToolResult, ToolSpec, Trajectory

_DEFAULT_TASKS_DIR = (
    Path(__file__).resolve().parents[2] / "external" / "terminal-bench-2"
)
# Match the upstream harness rather than inventing our own limits.
#
# Output: terminal-bench runs the agent against a tmux session started as
# `tmux new-session -x 160 -y 40` and reads it with `capture-pane` — so the
# reference agents (Terminus 1 and 2) see ONE SCREEN, 160x40 = 6400 chars.
# `capture_entire=True` (full scrollback) exists but is used only by the harness
# for logging, never by an agent. We therefore cap at 6400 and, crucially, keep
# the TAIL: a terminal screen shows the most recent output, which is where
# errors, test summaries and final results live. Keeping the head (as this
# adapter used to) hands the agent the preamble and throws the verdict away.
_MAX_OUTPUT = 6400
# Per-command BLOCK WAIT, matching real terminal-bench semantics. Upstream's
# `TmuxSession.send_keys` default is `max_timeout_sec = 180.0`, and crucially it
# is a *wait*, not a *kill*: on timeout the command KEEPS RUNNING in the terminal
# and the agent reads the screen again. We reproduce that below (persistent shell
# session; on timeout we return the output-so-far and leave the command running),
# instead of the old behaviour of hard-killing the command at this bound.
_CMD_WAIT = 180.0
_CLONE_HINT = (
    "Terminal-Bench 2.0 tasks not found at {d}. Clone them:\n"
    "  git clone https://github.com/laude-institute/terminal-bench-2 "
    "external/terminal-bench-2\n"
    "(or set TERMINAL_BENCH_TASKS / pass tasks_dir).")


# ----------------------------------------------------------- docker helpers
def _docker(args: list[str], endpoint: str | None, timeout: float | None = None,
            input: str | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if endpoint:
        env["DOCKER_HOST"] = endpoint
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          env=env, timeout=timeout, shell=False, input=input)


def _read_ctrf(container: str, endpoint: str | None
               ) -> tuple[list[str], int | None, int | None]:
    """Per-test detail (failed names, total, passed) from the verifier's CTRF json."""
    r = _docker(["exec", container, "cat", "/logs/verifier/ctrf.json"], endpoint, timeout=60)
    if r.returncode != 0:
        return [], None, None
    try:
        results = json.loads(r.stdout).get("results", {}) or {}
    except Exception:  # noqa: BLE001
        return [], None, None
    summary = results.get("summary", {}) or {}
    failed = [t.get("name") or "<unnamed>"
              for t in (results.get("tests") or [])
              if (t.get("status") or "").lower() not in ("passed", "skipped")]
    return failed, summary.get("tests"), summary.get("passed")


def _run_verifier(task: Task, container: str, endpoint: str | None) -> dict:
    """Copy the task's ``tests/`` into ``container``, run ``test.sh`` once, and read
    the reward + per-test detail. Returns a plain dict (``reward is None`` on any
    problem, with a ``note``); callers shape it into a Result / VerifierResult. This
    is the single source of truth for a task's grade — run in the REAL container so
    running servers, env and background state are all visible.
    """
    task_dir = task.metadata.get("task_dir")
    tests = Path(task_dir) / "tests" if task_dir else None
    if not tests or not (tests / "test.sh").exists():
        return {"reward": None, "note": f"no tests/test.sh under {tests}"}
    _docker(["exec", container, "mkdir", "-p", "/tests", "/logs/verifier"],
            endpoint, timeout=60)
    cp = _docker(["cp", f"{tests}/.", f"{container}:/tests/"], endpoint, timeout=120)
    if cp.returncode != 0:
        return {"reward": None,
                "note": "failed to copy tests: " + (cp.stderr or cp.stdout)[-300:]}
    # A Windows checkout (git autocrlf) leaves CRLF in the shell scripts, which
    # breaks bash in the Linux container ($'\r': command not found). Normalize.
    _docker(["exec", container, "sh", "-c",
             r"find /tests -name '*.sh' -exec sed -i 's/\r$//' {} + 2>/dev/null || true"],
            endpoint, timeout=60)
    timeout = float(task.metadata.get("verifier_timeout_sec", 900)) + 120
    try:
        tp = _docker(["exec", container, "bash", "/tests/test.sh"], endpoint, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"reward": None, "note": f"verifier timed out after {timeout:g}s"}
    r = _docker(["exec", container, "cat", "/logs/verifier/reward.txt"], endpoint, timeout=60)
    if r.returncode != 0:
        tail = ((tp.stdout or "") + (tp.stderr or ""))[-500:]
        return {"reward": None,
                "note": f"verifier wrote no reward.txt (exit {tp.returncode})", "tail": tail}
    try:
        reward = float((r.stdout or "").strip())
    except ValueError:
        return {"reward": None, "note": f"unparseable reward.txt: {(r.stdout or '')[:80]!r}"}
    failed, total, passed = _read_ctrf(container, endpoint)
    return {"reward": reward, "note": "official verifier (shared, in-container)",
            "failed_tests": failed, "tests_total": total, "tests_passed": passed}


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
        # Persistent-shell session state (real terminal-bench semantics). Lazily
        # started on the first command; falls back to a stateless exec if it can't.
        self._session: dict | None = None
        self._session_failed = False
        # The task's ONE official grading, cached so the reported score and the ACE
        # learning signal read the same evaluation (see official_verdict()).
        self._verdict: dict | None = None

    def observation(self) -> str:
        return self.task.prompt

    def instructions(self) -> str:
        return (
            "You are working inside a Linux container via the `bash` tool. "
            "Commands run in ONE persistent shell, so working directory, env vars "
            "and background processes PERSIST across calls (like a real terminal). "
            "A slow command is not killed at the wait limit — it keeps running and "
            "you can check back. When the task is complete, stop."
        )

    def tools(self) -> list[ToolSpec]:
        return [ToolSpec(
            name="bash",
            description="Run a shell command in the task's persistent Linux shell "
                        "and return its combined stdout/stderr.",
            parameters={"type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"]},
        )]

    # -- persistent session (real terminal-bench: one terminal, wait-not-kill) --
    def _sh(self, script: str, timeout: float = 60) -> subprocess.CompletedProcess:
        """One-off, out-of-session shell command in the container (for plumbing)."""
        return _docker(["exec", self.container, "sh", "-c", script],
                       self.endpoint, timeout=timeout)

    def _ensure_session(self) -> bool:
        """Start ONE long-lived bash reading commands from a FIFO, output appended
        to a log file. Because it is a single process, cwd / env / background jobs
        persist across commands; because we only ever *read* the log (never kill
        the shell), a slow command is never terminated. fd 9 is held open rw on the
        FIFO so external writers closing it don't send EOF and end the shell.
        Returns False if the container can't support it -> stateless fallback.
        """
        if self._session:
            return True
        if self._session_failed:
            return False
        sid = uuid.uuid4().hex[:8]
        fifo, log = f"/tmp/tb_{sid}.in", f"/tmp/tb_{sid}.log"
        if self._sh(f"mkfifo {fifo} && : > {log}", timeout=30).returncode != 0:
            self._session_failed = True
            return False
        # The persistent shell reads its command stream from the FIFO (as a script
        # via /dev/fd/9) but runs each command with **stdin = /dev/null**, not the
        # FIFO. This is essential: if executed commands inherited the FIFO as stdin,
        # an interactive one (e.g. apt-get's debconf "Geographic area:" prompt, or a
        # bare `python`/`cat`) would read the command stream and deadlock the session.
        # /dev/null gives it EOF instead — the same non-interactive behaviour the old
        # stateless `docker exec` had — while cwd / env / background jobs still persist
        # in the one shell. fd 9 is held rw so the FIFO never signals EOF and the
        # shell stays alive across commands. Commands are fed as raw bytes (see
        # _run_in_session) so no \r sneaks in from the Windows host.
        start = _docker(
            ["exec", "-d", self.container, "bash", "-c",
             f"exec 9<>{fifo}; exec bash --noprofile --norc /dev/fd/9 </dev/null "
             f">>{log} 2>&1"],
            self.endpoint, timeout=30)
        if start.returncode != 0:
            self._session_failed = True
            return False
        self._session = {"fifo": fifo, "log": log}
        return True

    def call_tool(self, name: str, arguments: dict) -> ToolResult:
        if name != "bash":
            return ToolResult(output=f"unknown tool {name!r}", is_error=True)
        cmd = (arguments or {}).get("command", "")
        self.n_commands += 1
        if self._ensure_session():
            return self._run_in_session(cmd)
        return self._run_stateless(cmd)

    def _run_in_session(self, cmd: str) -> ToolResult:
        s = self._session
        marker = "__TB_END_" + uuid.uuid4().hex + "__"
        wc = self._sh(f"wc -c < {s['log']}", timeout=30)
        try:
            before = int((wc.stdout or "0").strip() or 0)
        except ValueError:
            before = 0
        # Feed the command + a marker line carrying its exit code into the session.
        # Sent as raw BYTES (not _docker's text mode, which on Windows rewrites \n to
        # \r\n and makes every command arrive as ``cmd$'\r'``). No shell-quoting of
        # the command is needed because it travels over stdin.
        payload = (cmd + "\n" + f"printf '\\n{marker} %s\\n' \"$?\"\n").encode("utf-8")
        denv = dict(os.environ)
        if self.endpoint:
            denv["DOCKER_HOST"] = self.endpoint
        subprocess.run(
            ["docker", "exec", "-i", self.container, "bash", "-c",
             f"cat >> {s['fifo']}"],
            input=payload, env=denv, timeout=30,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Poll the log tail for the marker up to the block wait. On timeout we do
        # NOT kill: return the output so far; the command keeps running.
        deadline = time.monotonic() + _CMD_WAIT
        chunk, exit_code, running = "", None, True
        while True:
            r = self._sh(f"tail -c +{before + 1} {s['log']}", timeout=30)
            chunk = r.stdout or ""
            idx = chunk.find(marker)
            if idx != -1:
                tail = chunk[idx + len(marker):].strip().split()
                exit_code = (int(tail[0]) if tail and tail[0].lstrip("-").isdigit()
                             else None)
                chunk = chunk[:idx].rstrip("\n")
                running = False
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.4)
        return self._finish(chunk, exit_code, running)

    def _run_stateless(self, cmd: str) -> ToolResult:
        """Fallback when a persistent session can't be created: a plain exec with a
        hard wait (no persistence, no wait-not-kill — best effort)."""
        try:
            p = _docker(["exec", self.container, "bash", "-lc", cmd],
                        self.endpoint, timeout=_CMD_WAIT)
        except subprocess.TimeoutExpired:
            return ToolResult(
                output=f"[command timed out after {_CMD_WAIT:g}s]", is_error=True)
        return self._finish((p.stdout or "") + (p.stderr or ""), p.returncode, False)

    def _finish(self, out: str, exit_code: int | None, running: bool) -> ToolResult:
        if len(out) > _MAX_OUTPUT:
            # Keep the tail, like a terminal screen: the end holds the error and the
            # result. Say how much was dropped so the agent narrows the command.
            dropped = len(out) - _MAX_OUTPUT
            out = (f"[...{dropped} earlier chars omitted — this is the last "
                   f"{_MAX_OUTPUT} chars, as on a terminal screen. Re-run with "
                   f"head/grep/less if you need the start.]\n" + out[-_MAX_OUTPUT:])
        if running:
            out = (out + f"\n[still running after {_CMD_WAIT:g}s — the command keeps "
                   "running in the shell. Send another command to check on it "
                   "(read a log, `jobs`, `wait`), do not assume it finished.]")
            return ToolResult(output=out or "[no output yet]", is_error=False)
        if exit_code not in (0, None):
            out = f"[exit {exit_code}]\n{out}"
        return ToolResult(output=out or "[no output]", is_error=bool(exit_code))

    # -- the ONE official grading, shared by the report and the learner ---------
    def official_verdict(self) -> dict:
        """Grade this task in THIS (scored) container EXACTLY ONCE and cache it.

        Both the reported score (``TerminalBench.score``) and any in-episode learner
        (the ACE ``shared`` feedback mode) read this same result, so the learning
        signal can never disagree with the reported score — the standard
        "grade once, share the reward" contract. It replaces the old shadow-copy
        workaround, which graded a filesystem copy that lost running processes and
        so failed server tasks that had actually passed.
        """
        if self._verdict is None:
            self._verdict = _run_verifier(self.task, self.container, self.endpoint)
        return self._verdict

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
                    # The official PER-TASK agent budget. Terminal-Bench bounds
                    # an attempt by time, not by a step count, and the budget
                    # varies per task (750s .. 12000s across the 2.0 set), so a
                    # single global --timeout cannot express it. Agents enforce
                    # this themselves; the runner's global timeout must stay off
                    # (it also mis-attributes: it kills an arbitrary *pending*
                    # task, not the one actually running).
                    "agent_timeout_sec": float(
                        cfg.get("agent", {}).get("timeout_sec", 900)),
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
        # The ONE official grading. If the ACE learner already ran it this episode
        # (shared feedback mode), this returns that cached result — a single real
        # in-container grade shared by scoring and learning; otherwise it runs now.
        v = env.official_verdict()
        reward = v.get("reward")
        if reward is None:
            return Result(**base, success=False, score=0.0,
                          error=f"verifier produced no reward: {v.get('note', '')}"
                                + (f"\ntail:\n{v['tail']}" if v.get("tail") else ""),
                          metrics={"num_commands": env.n_commands})
        total = v.get("tests_total")
        return Result(
            **base, success=reward >= 1.0, score=float(reward),
            metrics={"reward": reward, "num_commands": env.n_commands,
                     "difficulty": task.metadata.get("difficulty"),
                     "tests_total": total, "tests_passed": v.get("tests_passed"),
                     "tests_failed": (None if total is None
                                      else total - (v.get("tests_passed") or 0))},
        )
