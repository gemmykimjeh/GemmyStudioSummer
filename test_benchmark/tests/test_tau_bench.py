"""Tests for the tau_bench adapter: MOCK fallback (offline) + REAL upstream.

The REAL tests drive the upstream simulator with a *human* user strategy (no
LLM/network) and score via the upstream reward, so they run offline as long as
the repo is cloned to external/tau-bench and litellm is installed. They are
skipped otherwise.
"""

from __future__ import annotations

import pytest

from harness.agents.scripted import ScriptedAgent
from harness.benchmarks._tau_upstream import upstream_available
from harness.benchmarks.tau_bench import TauBench
from harness.schema import Trajectory

# ---------------------------------------------------------------- MOCK path
def test_mock_oracle_solutions_pass():
    bench = TauBench(real=False)
    assert bench.mock is True
    for task in bench.load_tasks():
        env = bench.setup(task)
        traj = ScriptedAgent().run(task, env)
        result = bench.score(task, traj, env)
        assert result.success and result.score == 1.0


def test_mock_wrong_action_fails():
    bench = TauBench(real=False)
    task = bench.load_tasks(limit=1)[0]
    env = bench.setup(task)
    result = bench.score(task, Trajectory(final_state=env.snapshot()), env)
    assert not result.success  # did nothing -> goal unmet


# ---------------------------------------------------------------- REAL path
requires_upstream = pytest.mark.skipif(
    not upstream_available(),
    reason="upstream tau-bench not cloned to external/tau-bench (or litellm "
           "missing); see pip install -e '.[tau_bench]'",
)


def _real_bench() -> TauBench:
    # human user strategy => no LLM calls, fully offline.
    return TauBench(real=True, split="test", user_strategy="human")


def _retail_task0(bench: TauBench):
    tasks = bench.load_tasks()
    return next(t for t in tasks
                if t.metadata["domain"] == "retail"
                and t.metadata["task_index"] == 0)


@requires_upstream
def test_real_loads_both_domains():
    bench = _real_bench()
    tasks = bench.load_tasks()
    domains = {t.metadata["domain"] for t in tasks}
    assert {"retail", "airline"} <= domains
    assert len(tasks) == 115 + 50


@requires_upstream
def test_real_success_replaying_gold_actions():
    bench = _real_bench()
    task = _retail_task0(bench)
    env = bench.setup(task)
    for action in env.tau_env.task.actions:      # the gold trajectory
        env.call_tool(action.name, action.kwargs)
    result = bench.score(task, Trajectory(), env)
    assert result.success and result.score == 1.0
    assert result.metrics["domain"] == "retail"


@requires_upstream
def test_real_failure_early_termination():
    """(c) Agent stops without doing the task -> DB unchanged -> reward 0."""
    bench = _real_bench()
    task = _retail_task0(bench)
    env = bench.setup(task)
    result = bench.score(task, Trajectory(), env)   # no actions taken
    assert not result.success and result.score == 0.0


@requires_upstream
def test_real_failure_wrong_tool_args():
    """(b) A tool called with bad args errors; task not completed -> fail."""
    bench = _real_bench()
    task = _retail_task0(bench)
    env = bench.setup(task)
    res = env.call_tool("get_order_details", {"order_id": "#DOES_NOT_EXIST"})
    assert res.is_error
    result = bench.score(task, Trajectory(), env)
    assert not result.success


@requires_upstream
def test_real_failure_policy_violation_wrong_final_state():
    """(a) Agent performs a valid-but-incorrect mutation -> wrong DB -> fail."""
    bench = _real_bench()
    task = _retail_task0(bench)
    env = bench.setup(task)
    gold = env.tau_env.task.actions
    other = env.tau_env.tasks[1].actions  # same order, different exchange
    if gold[-1].name != "exchange_delivered_order_items" or \
            other[-1].name != "exchange_delivered_order_items":
        pytest.skip("upstream task 0/1 shape changed; policy-violation setup N/A")
    for action in gold[:-1]:                 # correct read steps
        env.call_tool(action.name, action.kwargs)
    env.call_tool(other[-1].name, other[-1].kwargs)  # wrong finalizer
    result = bench.score(task, Trajectory(), env)
    assert not result.success


@requires_upstream
def test_real_not_tagged_mock():
    assert _real_bench().mock is False
