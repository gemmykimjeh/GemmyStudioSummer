"""Tests for the Terminal-Bench 2.0 adapter.

Offline (always run): task loading from the local 2.0 task dir (task.toml +
instruction.md + prebuilt image), the task-id subset filter, and the no-Docker
precheck. A live scored run pulls per-task images + runs the official verifier,
so it is done manually.
"""

from __future__ import annotations

import pytest

from harness import registry
from harness.benchmarks import terminal_bench as tb
from harness.schema import Task


def test_registered_as_real_benchmark():
    registry.load_builtins()
    assert isinstance(registry.get_benchmark("terminal_bench"), tb.TerminalBench)


def test_load_tasks_reads_2_0_dataset():
    b = tb.TerminalBench()
    if not b.tasks_dir.is_dir():
        pytest.skip("terminal-bench-2 not cloned to external/terminal-bench-2")
    tasks = b.load_tasks(limit=3)
    assert tasks and all(t.benchmark == "terminal_bench" for t in tasks)
    for t in tasks:
        assert t.prompt.strip()
        # 2.0 tasks ship a prebuilt image + a verifier timeout
        assert t.metadata["docker_image"].startswith(("alexgshaw/", "ghcr.io/",
                                                       "docker.io/")) or ":" in t.metadata["docker_image"]
        assert t.metadata["verifier_timeout_sec"] > 0


def test_task_id_subset_filter():
    b = tb.TerminalBench()
    if not b.tasks_dir.is_dir():
        pytest.skip("terminal-bench-2 not cloned")
    all_ids = [t.id for t in b.load_tasks()]
    pick = all_ids[0]
    only = tb.TerminalBench(task_ids=pick).load_tasks()
    assert [t.id for t in only] == [pick]


def test_precheck_errors_without_docker(monkeypatch):
    b = tb.TerminalBench(env_endpoint="tcp://127.0.0.1:1")  # unreachable
    monkeypatch.setattr(tb, "_docker_ok", lambda ep: False)
    task = Task(id="x", benchmark="terminal_bench", prompt="do",
                metadata={"task_dir": ".", "docker_image": "img:latest"})
    with pytest.raises(RuntimeError, match="Docker"):
        b.setup(task)
