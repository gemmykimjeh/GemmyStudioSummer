"""swe_bench — SWE-Bench Verified GitHub issue resolution (real).

(a) License:        MIT (swe-bench/SWE-bench). Dataset: princeton-nlp/SWE-bench_Verified.
(b) Infrastructure: HEAVY. Docker only (no host Python deps for scoring). The
                    agent edits a git checkout in a lightweight container; scoring
                    runs the OFFICIAL ``swebench.harness.run_evaluation`` inside a
                    small Linux "runner" image (built on demand) that drives the
                    host Docker daemon via the mounted socket -- swebench is
                    Unix-only (imports the ``resource`` module), so it cannot run
                    on native Windows Python. The harness builds per-instance
                    (multi-GB) images, applies the patch, and runs the tests. Use
                    local Docker or ``--env-endpoint`` (DOCKER_HOST). Missing/broken
                    Docker -> precise precheck error.
(c) Scoring:        the official harness — an instance is *resolved* iff all
                    FAIL_TO_PASS and PASS_TO_PASS tests pass after applying the
                    model patch. Reported 0/1. No substitution.
(d) Official repo:  https://github.com/swe-bench/SWE-bench

Flow: load_tasks (HF dataset) -> setup (clone repo @ base_commit into /testbed in
a container; agent gets a `bash` tool) -> agent edits -> score (git diff = the
model_patch -> predictions.jsonl -> run_evaluation -> parse resolved).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from harness.benchmark import Benchmark, Env
from harness.registry import register_benchmark
from harness.schema import Result, Task, ToolResult, ToolSpec, Trajectory

_DATASET = "princeton-nlp/SWE-bench_Verified"
_MAX_OUTPUT = 4000

# The official swebench harness is Unix-only, so we run it inside this tiny Linux
# image (built on demand) which talks to the host Docker daemon via the mounted
# socket. This makes scoring work identically on Windows/macOS/Linux.
_RUNNER_IMAGE = "harness-swebench-runner:latest"
_RUNNER_DOCKERFILE = """\
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \\
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir swebench
WORKDIR /work
"""


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


def _image_exists(image: str, endpoint: str | None) -> bool:
    try:
        return _docker(["image", "inspect", image], endpoint, timeout=30).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _ensure_runner_image(image: str, endpoint: str | None, timeout: float) -> None:
    """Build the Linux swebench-runner image if it is not present.

    Raises RuntimeError with actionable guidance if the build (hence Docker) fails.
    """
    if _image_exists(image, endpoint):
        return
    with tempfile.TemporaryDirectory(prefix="swe_runner_") as bd:
        Path(bd, "Dockerfile").write_text(_RUNNER_DOCKERFILE, encoding="utf-8")
        try:
            b = _docker(["build", "-t", image, bd], endpoint, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - docker missing / not running
            raise RuntimeError(
                f"could not build the swebench-runner image {image!r}: {exc}. "
                "Is Docker installed and running?") from exc
    if b.returncode != 0:
        raise RuntimeError(
            f"failed to build the swebench-runner image {image!r} (needs Docker "
            "with network access):\n" + (b.stderr or b.stdout)[-1500:])


class SWEBenchEnv(Env):
    """A container with the repo cloned at base_commit; agent acts via `bash`."""

    WORKDIR = "/testbed"

    def __init__(self, task: Task, image: str, container: str,
                 endpoint: str | None) -> None:
        self.task = task
        self.image = image
        self.container = container
        self.endpoint = endpoint
        self.n_commands = 0
        self._local_ws: str | None = None  # A-mode: lazy local repo checkout

    def observation(self) -> str:
        return self.task.prompt

    def instructions(self) -> str:
        return (
            "You are fixing a real GitHub issue in a Python repository checked "
            f"out at {self.WORKDIR} (at the base commit). Use the `bash` tool to "
            "read and edit the source and resolve the issue described. Do NOT "
            "edit tests — only the library/source code. When done, stop; your "
            "git diff will be evaluated against the project's tests."
        )

    def tools(self) -> list[ToolSpec]:
        return [ToolSpec(
            name="bash",
            description=f"Run a shell command in the repo at {self.WORKDIR} and "
                        "return combined stdout/stderr.",
            parameters={"type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"]},
        )]

    def _exec(self, cmd: str, timeout: float = 120) -> subprocess.CompletedProcess:
        return _docker(["exec", "-w", self.WORKDIR, self.container,
                        "bash", "-lc", cmd], self.endpoint, timeout=timeout)

    def call_tool(self, name: str, arguments: dict) -> ToolResult:
        if name != "bash":
            return ToolResult(output=f"unknown tool {name!r}", is_error=True)
        self.n_commands += 1
        try:
            p = self._exec((arguments or {}).get("command", ""))
        except subprocess.TimeoutExpired:
            return ToolResult(output="[command timed out after 120s]", is_error=True)
        out = (p.stdout or "") + (p.stderr or "")
        if len(out) > _MAX_OUTPUT:
            out = out[:_MAX_OUTPUT] + "\n...[truncated]"
        if p.returncode != 0:
            out = f"[exit {p.returncode}]\n{out}"
        return ToolResult(output=out or "[no output]", is_error=p.returncode != 0)

    def workspace(self) -> str:
        """A-mode: a LOCAL clone of the repo @ base_commit that a native-tool
        agent (e.g. claude_agent_sdk --native-tools) edits with its own tools.
        Cloned lazily on first request; ``model_patch`` then diffs this dir."""
        if self._local_ws is None:
            repo = self.task.metadata["repo"]
            base = self.task.metadata["base_commit"]
            ws = tempfile.mkdtemp(prefix="swe_ws_")
            clone = subprocess.run(
                ["git", "clone", "--quiet", f"https://github.com/{repo}", ws],
                capture_output=True, text=True, timeout=600)
            if clone.returncode != 0:
                shutil.rmtree(ws, ignore_errors=True)
                raise RuntimeError(
                    f"A-mode: failed to clone {repo} locally: "
                    + (clone.stderr or clone.stdout)[-500:])
            co = subprocess.run(["git", "-C", ws, "checkout", "-q", base],
                                capture_output=True, text=True, timeout=120)
            if co.returncode != 0:
                shutil.rmtree(ws, ignore_errors=True)
                raise RuntimeError(f"A-mode: failed to checkout {base}: "
                                   + (co.stderr or co.stdout)[-300:])
            for k, v in (("user.email", "a@b.c"), ("user.name", "a")):
                subprocess.run(["git", "-C", ws, "config", k, v],
                               capture_output=True)
            self._local_ws = ws
        return self._local_ws

    def model_patch(self) -> str:
        """The agent's changes as a unified diff (incl. new files).

        A-mode: diff the local workspace the native tools edited. B-mode: diff the
        container the `bash` tool acted in."""
        if self._local_ws is not None:
            subprocess.run(["git", "-C", self._local_ws, "add", "-A"],
                           capture_output=True, timeout=120)
            p = subprocess.run(["git", "-C", self._local_ws, "diff", "--cached"],
                               capture_output=True, text=True, timeout=120)
            return p.stdout or ""
        p = self._exec("git add -A && git diff --cached", timeout=120)
        return p.stdout or ""

    def snapshot(self) -> dict:
        return {"container": self.container, "commands": self.n_commands,
                "mode": "native" if self._local_ws else "tool-bridge"}

    def close(self) -> None:
        if self._local_ws:
            shutil.rmtree(self._local_ws, ignore_errors=True)
            self._local_ws = None


@register_benchmark("swe_bench")
class SWEBench(Benchmark):
    """SWE-Bench Verified; official swebench harness scoring over Docker."""

    def __init__(
        self,
        env_endpoint: str | None = None,
        dataset_name: str = _DATASET,
        split: str = "test",
        base_image: str = "buildpack-deps:bookworm-scm",  # has git + bash
        model_name: str = "harness_claude_sdk",
        eval_timeout: float = 5400.0,
        setup_timeout: float = 1800.0,
        runner_image: str = _RUNNER_IMAGE,
        docker_sock: str = "/var/run/docker.sock",
    ) -> None:
        self.env_endpoint = env_endpoint or os.environ.get("SWEBENCH_ENDPOINT")
        self.dataset_name = dataset_name
        self.split = split
        self.base_image = base_image
        self.model_name = model_name
        self.eval_timeout = eval_timeout
        self.setup_timeout = setup_timeout
        self.runner_image = runner_image
        self.docker_sock = docker_sock

    # -- tasks ------------------------------------------------------------
    def load_tasks(self, limit: int | None = None) -> list[Task]:
        try:
            from datasets import load_dataset  # lazy
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "SWE-Bench needs the datasets package: "
                'pip install -e ".[swe_bench]".') from exc
        ds = load_dataset(self.dataset_name, split=self.split)
        rows = ds.select(range(min(limit, len(ds)))) if limit is not None else ds
        tasks = []
        for r in rows:
            tasks.append(Task(
                id=r["instance_id"], benchmark="swe_bench",
                prompt="# GitHub issue to resolve\n\n" + r["problem_statement"],
                metadata={"instance_id": r["instance_id"], "repo": r["repo"],
                          "base_commit": r["base_commit"]},
            ))
        return tasks

    # -- setup ------------------------------------------------------------
    def _precheck_docker(self) -> None:
        if not _docker_ok(self.env_endpoint):
            where = (f"the Docker endpoint {self.env_endpoint!r}"
                     if self.env_endpoint else "the local Docker daemon")
            raise RuntimeError(
                f"swe_bench needs Docker, but {where} is not reachable.\n"
                "  - Local: install/start Docker Desktop (WSL2 on Windows).\n"
                "  - Remote: pass --env-endpoint tcp://HOST:2375 (or DOCKER_HOST).")

    def setup(self, task: Task) -> Env:
        self._precheck_docker()
        uid = uuid.uuid4().hex[:8]
        safe = re.sub(r"[^a-z0-9]+", "-", task.id.lower())[:40]
        container = f"swe-{safe}-{uid}"
        repo = task.metadata["repo"]
        base = task.metadata["base_commit"]
        run = _docker(["run", "-d", "--name", container, self.base_image,
                       "sleep", "infinity"], self.env_endpoint, timeout=300)
        if run.returncode != 0:
            raise RuntimeError(f"docker run failed for {task.id}:\n"
                               + (run.stderr or run.stdout)[-1500:])
        clone = _docker(
            ["exec", container, "bash", "-lc",
             f"git clone https://github.com/{repo} /testbed "
             f"&& cd /testbed && git checkout -q {base} && git config user.email a@b.c "
             f"&& git config user.name a"],
            self.env_endpoint, timeout=self.setup_timeout)
        if clone.returncode != 0:
            _docker(["rm", "-f", container], self.env_endpoint)
            raise RuntimeError(
                f"failed to clone {repo}@{base} for {task.id}:\n"
                + (clone.stderr or clone.stdout)[-1500:])
        return SWEBenchEnv(task, self.base_image, container, self.env_endpoint)

    def teardown(self, env: Env) -> None:
        if isinstance(env, SWEBenchEnv):
            _docker(["rm", "-f", env.container], env.endpoint)
            env.close()  # remove the A-mode local workspace, if any

    # -- scoring (official harness) --------------------------------------
    def score(self, task: Task, trajectory: Trajectory, env: Env) -> Result:
        assert isinstance(env, SWEBenchEnv)
        base = dict(task_id=task.id, benchmark=self.name, agent="",
                    cost=trajectory.cost, wall_time=trajectory.wall_time)
        patch = env.model_patch()
        if not patch.strip():
            return Result(**base, success=False, score=0.0,
                          metrics={"empty_patch": True, "num_commands": env.n_commands})
        # swebench is Unix-only; run the OFFICIAL harness inside the Linux runner
        # image, which drives the host Docker daemon via the mounted socket.
        try:
            _ensure_runner_image(self.runner_image, env.endpoint, self.setup_timeout)
        except RuntimeError as exc:
            return Result(
                **base, success=False, score=0.0, error=str(exc),
                metrics={"empty_patch": False, "patch_bytes": len(patch),
                         "num_commands": env.n_commands})

        run_id = f"harness-{uuid.uuid4().hex[:8]}"
        with tempfile.TemporaryDirectory(prefix="swe_eval_") as td:
            preds = Path(td) / "preds.jsonl"
            preds.write_text(json.dumps({
                "instance_id": task.id,
                "model_name_or_path": self.model_name,
                "model_patch": patch,
            }) + "\n", encoding="utf-8")
            # Mount the temp dir at /work (predictions in, report out) and give the
            # runner access to the daemon: the local socket, or DOCKER_HOST for a
            # remote endpoint.
            run_args = ["run", "--rm", "-v", f"{Path(td).as_posix()}:/work", "-w", "/work"]
            if env.endpoint:
                run_args += ["-e", f"DOCKER_HOST={env.endpoint}"]
            else:
                run_args += ["-v", f"{self.docker_sock}:/var/run/docker.sock"]
            run_args += [
                self.runner_image, "python", "-m", "swebench.harness.run_evaluation",
                "--dataset_name", self.dataset_name, "--split", self.split,
                "--predictions_path", "/work/preds.jsonl", "--run_id", run_id,
                "--instance_ids", task.id, "--max_workers", "1"]
            try:
                ev = _docker(run_args, env.endpoint, timeout=self.eval_timeout)
            except subprocess.TimeoutExpired:
                return Result(**base, success=False, score=0.0,
                              error=f"swebench eval timed out after {self.eval_timeout}s",
                              metrics={"patch_bytes": len(patch)})
            resolved = self._parse_resolved(Path(td), task.id, ev.stdout + ev.stderr)
        return Result(
            **base, success=resolved, score=1.0 if resolved else 0.0,
            metrics={"resolved": resolved, "empty_patch": False,
                     "patch_bytes": len(patch), "num_commands": env.n_commands},
        )

    def _parse_resolved(self, report_dir: Path, instance_id: str, stdout: str) -> bool:
        # Preferred: the aggregate "<model>.<run_id>.json" report the harness writes.
        for jf in report_dir.glob("*.json"):
            try:
                rep = json.loads(jf.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if instance_id in rep.get("resolved_ids", []):
                return True
        # Fallback: per-instance report.json under the harness log dir.
        for rf in report_dir.rglob("report.json"):
            try:
                rep = json.loads(rf.read_text(encoding="utf-8"))
                if rep.get(instance_id, {}).get("resolved"):
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False
