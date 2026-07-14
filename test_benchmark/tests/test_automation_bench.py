"""Tests for automation_bench: MOCK fallback + REAL upstream (offline).

AutomationBench has no user-simulator LLM — scoring is deterministic assertion
checks — so the REAL path runs fully offline (no API key). Real tests are
skipped only if the upstream repo isn't cloned / datasets isn't installed.
"""

from __future__ import annotations

import pytest

from harness.agents.scripted import ScriptedAgent
from harness.benchmarks._automation_upstream import upstream_available
from harness.benchmarks.automation_bench import AutomationBench
from harness.schema import Step, Trajectory

# ------------------------------------------------------------------ MOCK
def test_mock_oracle_solutions_pass():
    bench = AutomationBench(real=False)
    assert bench.mock is True
    for task in bench.load_tasks():
        env = bench.setup(task)
        traj = ScriptedAgent().run(task, env)
        result = bench.score(task, traj, env)
        assert result.success and result.score == 1.0


def test_mock_empty_trajectory_fails():
    bench = AutomationBench(real=False)
    task = bench.load_tasks(limit=1)[0]
    env = bench.setup(task)
    result = bench.score(task, Trajectory(final_state=env.snapshot()), env)
    assert not result.success


# ------------------------------------------------------------------ REAL
requires_upstream = pytest.mark.skipif(
    not upstream_available(),
    reason="upstream AutomationBench not cloned to external/AutomationBench "
           "(or datasets missing); see pip install -e '.[automation_bench]'",
)


@requires_upstream
def test_real_loads_six_domains_simple_excluded():
    bench = AutomationBench(real=True)
    tasks = bench.load_tasks()
    domains = {t.metadata["domain"] for t in tasks}
    assert domains == {"sales", "marketing", "operations", "support",
                       "finance", "hr"}
    assert "simple" not in domains
    assert len(tasks) == 600
    assert not any(t.metadata["baseline_only"] for t in tasks)


@requires_upstream
def test_real_simple_is_baseline_only_when_included():
    bench = AutomationBench(real=True, include_simple=True)
    tasks = bench.load_tasks()
    simple = [t for t in tasks if t.metadata["domain"] == "simple"]
    assert len(simple) == 200
    assert all(t.metadata["baseline_only"] for t in simple)
    # baseline tasks are excluded from the official report aggregate
    assert all(not t.metadata["baseline_only"]
               for t in tasks if t.metadata["domain"] != "simple")


@requires_upstream
def test_real_deterministic_scoring_and_noop_fails():
    """No-op agent -> task not completed -> success False, valid Result."""
    bench = AutomationBench(real=True, domains=("sales",))
    task = bench.load_tasks(limit=1)[0]
    env = bench.setup(task)
    result = bench.score(task, ScriptedAgent().run(task, env), env)
    assert result.success is False
    assert 0.0 <= result.score <= 1.0
    # success is exactly task_completed_correctly == 1.0 (strict, deterministic)
    assert result.metrics["task_completed_correctly"] == (1.0 if result.success else 0.0)
    assert result.mock is False


@requires_upstream
def test_real_scoring_is_repeatable():
    bench = AutomationBench(real=True, domains=("finance",))
    task = bench.load_tasks(limit=1)[0]
    s1 = bench.score(task, Trajectory(), bench.setup(task)).score
    s2 = bench.score(task, Trajectory(), bench.setup(task)).score
    assert s1 == s2  # deterministic, no LLM judge


@requires_upstream
def test_real_false_success_claim_detected():
    """Agent 'declares success' but final state fails -> flagged (req 4)."""
    bench = AutomationBench(real=True, domains=("sales",))
    task = bench.load_tasks(limit=1)[0]
    env = bench.setup(task)  # no actions -> task fails
    claim = Trajectory(final_output="All done! I've completed the task successfully.")
    r = bench.score(task, claim, env)
    assert r.success is False
    assert r.metrics["claimed_success"] is True
    assert r.metrics["false_success_claim"] is True

    # A neutral final message with the same failing state is NOT a false claim.
    neutral = Trajectory(final_output="I looked at the records but stopped.")
    r2 = bench.score(task, neutral, bench.setup(task))
    assert r2.metrics["false_success_claim"] is False


@requires_upstream
def test_real_report_has_domains_and_caveat():
    bench = AutomationBench(real=True, domains=("sales",))
    task = bench.load_tasks(limit=1)[0]
    r = bench.score(task, Trajectory(), bench.setup(task))
    r.agent = "x"
    text = bench.report([r])
    assert text is not None
    assert "pass_rate" in text and "false_success" in text
    assert "PUBLIC task set" in text  # public-vs-official caveat


@requires_upstream
def test_real_not_tagged_mock():
    assert AutomationBench(real=True).mock is False
