"""Tests for the SWE-Bench Verified adapter.

Offline (always run): report parsing, registry wiring, the no-Docker precheck,
and the missing-swebench scoring guidance. A real scored instance needs Docker +
the swebench harness + multi-GB images and is run manually.
"""

from __future__ import annotations

import importlib.util
import json

import pytest

from harness import registry
from harness.benchmarks import swe_bench as sb
from harness.schema import Task, Trajectory


def test_parse_resolved_aggregate_and_perinstance(tmp_path):
    b = sb.SWEBench()
    # aggregate report form
    (tmp_path / "harness.run.json").write_text(
        json.dumps({"resolved_ids": ["astropy__astropy-12907"]}), encoding="utf-8")
    assert b._parse_resolved(tmp_path, "astropy__astropy-12907", "") is True
    assert b._parse_resolved(tmp_path, "other__id-1", "") is False
    # per-instance report.json fallback
    d = tmp_path / "logs" / "x"
    d.mkdir(parents=True)
    (d / "report.json").write_text(
        json.dumps({"django__django-1": {"resolved": True}}), encoding="utf-8")
    assert b._parse_resolved(tmp_path, "django__django-1", "") is True


def test_registered_as_real_benchmark():
    registry.load_builtins()
    assert isinstance(registry.get_benchmark("swe_bench"), sb.SWEBench)


def test_setup_precheck_requires_docker(monkeypatch):
    monkeypatch.setattr(sb, "_docker_ok", lambda ep: False)
    b = sb.SWEBench(env_endpoint="tcp://127.0.0.1:1")
    task = Task(id="x", benchmark="swe_bench", prompt="p",
                metadata={"repo": "a/b", "base_commit": "abc"})
    with pytest.raises(RuntimeError, match="Docker"):
        b.setup(task)


def test_score_guidance_when_runner_unavailable(monkeypatch):
    # If the Linux swebench-runner image can't be built (e.g. Docker down),
    # score() surfaces the actionable error instead of a bogus number.
    def boom(*a, **k):
        raise RuntimeError("failed to build the swebench-runner image (needs Docker)")
    monkeypatch.setattr(sb, "_ensure_runner_image", boom)
    b = sb.SWEBench()
    task = Task(id="astropy__astropy-1", benchmark="swe_bench", prompt="p",
                metadata={"repo": "astropy/astropy", "base_commit": "abc"})
    env = sb.SWEBenchEnv(task, "img", "ctr", None)
    monkeypatch.setattr(env, "model_patch", lambda: "diff --git a/x b/x\n+fix\n")
    r = b.score(task, Trajectory(), env)
    assert r.success is False
    assert "runner" in (r.error or "").lower()
    assert r.metrics["patch_bytes"] > 0


def test_env_is_a_mode_capable():
    # swe_bench exposes a local workspace() -> A-mode (native-tool) capable.
    task = Task(id="astropy__astropy-1", benchmark="swe_bench", prompt="p",
                metadata={"repo": "astropy/astropy", "base_commit": "abc"})
    env = sb.SWEBenchEnv(task, "img", "ctr", None)
    assert env._local_ws is None                    # not cloned until requested
    assert callable(env.workspace)                  # A-capable signal present
    from harness.benchmark import Env
    assert Env.workspace(env) is None               # base default is B-only


def test_score_empty_patch_fails(monkeypatch):
    b = sb.SWEBench()
    task = Task(id="astropy__astropy-1", benchmark="swe_bench", prompt="p",
                metadata={"repo": "astropy/astropy", "base_commit": "abc"})
    env = sb.SWEBenchEnv(task, "img", "ctr", None)
    monkeypatch.setattr(env, "model_patch", lambda: "   \n")
    r = b.score(task, Trajectory(), env)
    assert r.success is False and r.metrics["empty_patch"] is True


@pytest.mark.skipif(importlib.util.find_spec("datasets") is None,
                    reason="datasets not installed")
def test_load_tasks_reads_verified(tmp_path):
    try:
        tasks = sb.SWEBench().load_tasks(limit=2)
    except Exception as exc:  # noqa: BLE001 - offline / HF unreachable
        pytest.skip(f"SWE-bench_Verified not available offline: {exc}")
    assert len(tasks) == 2
    for t in tasks:
        assert t.benchmark == "swe_bench"
        assert t.metadata["repo"] and t.metadata["base_commit"]
        assert t.prompt.strip()
