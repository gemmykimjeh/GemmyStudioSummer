"""Tests for the orchestrator: end-to-end run, checkpoint/resume, isolation."""

from __future__ import annotations

import json
import time

from harness.agents.scripted import ScriptedAgent
from harness.benchmark import Env
from harness.benchmarks.automation_bench import AutomationBench, AutomationMockEnv
from harness.benchmarks.tau_bench import TauBench
from harness.runner import compute_pass_hat_k, run, summarize
from harness.schema import Result, Task, Trajectory


def test_run_end_to_end_all_pass(tmp_path):
    results = run(ScriptedAgent(), TauBench(real=False), output_dir=tmp_path, concurrency=2)
    assert len(results) == 4
    assert all(r.success for r in results)
    assert all(r.agent == "scripted" for r in results)

    run_dir = tmp_path / "tau_bench__scripted"
    assert (run_dir / "results.jsonl").exists()
    assert (run_dir / "summary.csv").exists()
    stats = summarize(results)
    assert stats["success_rate"] == 1.0


def test_checkpoint_and_resume(tmp_path):
    run(ScriptedAgent(), AutomationBench(real=False), output_dir=tmp_path, limit=2)
    ckpt = tmp_path / "automation_bench__scripted" / "results.jsonl"
    first = [json.loads(l) for l in ckpt.read_text().splitlines() if l.strip()]
    assert len(first) == 2

    # Resume with the full set: only the remaining task runs, checkpoint grows.
    results = run(ScriptedAgent(), AutomationBench(real=False),
                  output_dir=tmp_path, resume=True)
    all_lines = [json.loads(l) for l in ckpt.read_text().splitlines() if l.strip()]
    ids = {r["task_id"] for r in all_lines}
    assert ids == {"auto-001", "auto-002", "auto-003"}
    # run() returns only the newly-executed task on resume
    assert {r.task_id for r in results} == {"auto-003"}


class _FlakyBench(AutomationBench):
    """AutomationBench (mock) that explodes during setup for one task id."""

    name = "flaky"

    def __init__(self) -> None:
        super().__init__(real=False)

    def setup(self, task: Task) -> Env:
        if task.id == "auto-002":
            raise RuntimeError("boom in setup")
        return AutomationMockEnv(task)


def test_failure_isolation(tmp_path):
    results = run(ScriptedAgent(), _FlakyBench(), output_dir=tmp_path,
                  concurrency=1)
    by_id = {r.task_id: r for r in results}
    # the exploding task becomes an error result, the rest still succeed
    assert by_id["auto-002"].error is not None
    assert not by_id["auto-002"].success
    assert by_id["auto-001"].success
    assert by_id["auto-003"].success


class _SlowAgent(ScriptedAgent):
    name = "slow"

    def run(self, task: Task, env: Env) -> Trajectory:
        time.sleep(1.0)
        return super().run(task, env)


def test_per_task_timeout(tmp_path):
    results = run(_SlowAgent(), TauBench(real=False), output_dir=tmp_path,
                  limit=1, concurrency=1, timeout=0.2)
    assert len(results) == 1
    assert results[0].error is not None
    assert "timeout" in results[0].error


def test_mock_benchmarks_tag_results(tmp_path):
    results = run(ScriptedAgent(), TauBench(real=False), output_dir=tmp_path, limit=1)
    assert all(r.mock for r in results)
    # CSV carries the [MOCK] tag column
    csv_text = (tmp_path / "tau_bench__scripted" / "summary.csv").read_text()
    assert "mock" in csv_text.splitlines()[0]
    assert "[MOCK]" in csv_text
    # JSONL carries the flag too
    jsonl = (tmp_path / "tau_bench__scripted" / "results.jsonl").read_text()
    assert json.loads(jsonl.splitlines()[0])["mock"] is True


def _r(tid, trial, ok, domain="d"):
    return Result(task_id=tid, benchmark="b", agent="a", trial=trial,
                  success=ok, score=1.0 if ok else 0.0, metrics={"domain": domain})


def test_pass_hat_k_combinatorial():
    # task A: 3/3 pass; task B: 1/3 pass; evaluate pass^2
    results = [
        _r("A", 0, True), _r("A", 1, True), _r("A", 2, True),
        _r("B", 0, True), _r("B", 1, False), _r("B", 2, False),
    ]
    ov = compute_pass_hat_k(results, 2)["__overall__"]
    assert abs(ov["pass_at_1"] - 4 / 6) < 1e-9   # per-trial success rate
    # pass^2: A = C(3,2)/C(3,2)=1, B = c(1)<2 -> 0, mean = 0.5
    assert abs(ov["pass_hat_k"] - 0.5) < 1e-9
    assert ov["n_tasks"] == 2 and ov["trials"] == 6


def test_runner_k_trials_and_passk_csv(tmp_path):
    results = run(ScriptedAgent(), TauBench(real=False), output_dir=tmp_path,
                  limit=2, k=3)
    # 2 tasks x 3 trials = 6 results, each with a trial index
    assert len(results) == 6
    assert {r.trial for r in results} == {0, 1, 2}
    passk_csv = (tmp_path / "tau_bench__scripted" / "passk_summary.csv").read_text()
    assert "pass^3" in passk_csv
    # deterministic scripted agent -> every task passes all 3 trials -> pass^3 = 1
    ov = compute_pass_hat_k(results, 3)["__overall__"]
    assert ov["pass_hat_k"] == 1.0


def test_summarize_empty():
    assert summarize([]) == {
        "n": 0, "success_rate": 0.0, "mean_score": 0.0,
        "total_cost": 0.0, "errors": 0,
    }
