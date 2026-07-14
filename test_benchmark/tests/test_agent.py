"""Unit tests for the Agent interface and its adapters."""

from __future__ import annotations

import pytest

from harness.agent import Agent
from harness.agents.claude_sdk import ClaudeSDKAgent, _price_for
from harness.agents.scripted import ScriptedAgent
from harness.benchmarks.tau_bench import TauBench
from harness.schema import Task, Trajectory


def test_agent_is_abstract():
    with pytest.raises(TypeError):
        Agent()  # type: ignore[abstract]


def test_scripted_agent_runs_oracle_actions():
    bench = TauBench(real=False)
    task = bench.load_tasks(limit=1)[0]  # tau-retail-001: cancel O1001
    env = bench.setup(task)
    agent = ScriptedAgent()
    traj = agent.run(task, env)

    assert isinstance(traj, Trajectory)
    # oracle: cancel_order + message_user -> 2 tool calls recorded
    assert len(traj.tool_calls()) == 2
    # final state reflects the cancellation
    assert traj.final_state["orders"]["O1001"]["status"] == "cancelled"


def test_scripted_agent_no_actions_is_noop():
    task = Task(id="x", benchmark="tau_bench", prompt="nothing", metadata={})
    env = TauBench(real=False).setup(task)
    traj = ScriptedAgent().run(task, env)
    assert traj.tool_calls() == []


def test_scripted_agent_rejects_unknown_tool():
    task = Task(id="x", benchmark="tau_bench", prompt="p",
                metadata={"oracle_actions": [{"tool": "nope", "arguments": {}}]})
    env = TauBench(real=False).setup(task)
    traj = ScriptedAgent().run(task, env)
    assert any(s.type == "error" and s.is_error for s in traj.steps)


def test_claude_pricing_lookup():
    assert _price_for("claude-opus-4-8") == (5.0, 25.0)
    assert _price_for("unknown-model") == (0.0, 0.0)


def test_claude_agent_requires_key_or_sdk(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    agent = ClaudeSDKAgent(api_key=None)
    task = TauBench(real=False).load_tasks(limit=1)[0]
    env = TauBench(real=False).setup(task)
    # Without an SDK/key installed+set, run must fail loudly (not silently).
    with pytest.raises(RuntimeError):
        agent.run(task, env)
