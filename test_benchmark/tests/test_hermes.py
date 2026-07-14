"""Tests for the Hermes integration: MCP bridge, agent wiring, compare table.

These run offline (no Hermes subprocess, no network) — the live subprocess run
is a separate manual wiring check. They cover the pieces we own: the in-process
MCP bridge, HermesAgent preflight + trajectory parsing, and the compare table.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from harness.agents._mcp_bridge import EnvMCPBridge
from harness.agents._react import messages_to_trajectory
from harness.agents.hermes import HermesAgent
from harness.benchmarks.automation_bench import AutomationBench
from harness.compare import build_comparison
from harness.schema import Trajectory


def test_hermes_registered():
    from harness import registry
    registry.load_builtins()
    assert "hermes" in registry.available_agents()
    assert isinstance(registry.get_agent("hermes"), HermesAgent)


def test_bridge_lists_and_calls_env_tools():
    bench = AutomationBench(real=False)
    task = bench.load_tasks(limit=1)[0]  # auto-001
    env = bench.setup(task)
    bridge = EnvMCPBridge(env)
    url = bridge.start()
    try:
        async def flow():
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
            async with streamablehttp_client(url) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    tools = [t.name for t in (await s.list_tools()).tools]
                    res = await s.call_tool(
                        "tracker_create_task",
                        {"title": "Follow up with Acme", "assignee": "alice"})
                    return tools, res.content[0].text

        tools, out = asyncio.run(flow())
        assert "tracker_create_task" in tools
        assert "created task" in out
        # the in-process env was mutated by the remote MCP call
        assert env.snapshot()["tracker"][0]["title"] == "Follow up with Acme"
        assert bench.score(task, Trajectory(), env).success
    finally:
        bridge.stop()


def test_hermes_preflight_raises_when_missing(tmp_path):
    agent = HermesAgent(hermes_path=str(tmp_path))  # no run_agent.py here
    bench = AutomationBench(real=False)
    task = bench.load_tasks(limit=1)[0]
    env = bench.setup(task)
    with pytest.raises(RuntimeError, match="Hermes not found"):
        agent.run(task, env)


def test_hermes_native_mode_errors_on_b_only(monkeypatch):
    # A-mode (native tools) on a B-only benchmark -> clear, actionable error.
    agent = HermesAgent(native_tools=True)
    monkeypatch.setattr(agent, "_preflight", lambda: None)  # skip Hermes-install check
    bench = AutomationBench(real=False)
    task = bench.load_tasks(limit=1)[0]
    env = bench.setup(task)   # automation_bench is B-only (no local workspace())
    with pytest.raises(RuntimeError, match="B-mode ONLY"):
        agent.run(task, env)


def test_hermes_native_tools_flag():
    assert HermesAgent(native_tools=True).native_tools is True
    assert HermesAgent().native_tools is False   # B-mode default


def test_parse_messages_openai_shape():
    traj = Trajectory()
    messages_to_trajectory([
        {"role": "user", "content": "do it"},
        {"role": "assistant", "content": "let me try",
         "tool_calls": [{"function": {"name": "foo", "arguments": '{"a": 1}'}}]},
        {"role": "tool", "content": "ok result"},
        {"role": "assistant", "content": "done"},
    ], traj)
    calls = traj.tool_calls()
    assert len(calls) == 1
    assert calls[0].name == "foo"
    assert calls[0].arguments == {"a": 1}
    assert calls[0].output == "ok result"
    assert any(s.type == "assistant_message" and s.text == "done" for s in traj.steps)


def test_parse_messages_sharegpt_shape():
    traj = Trajectory()
    messages_to_trajectory([
        {"from": "human", "value": "hi"},
        {"from": "gpt", "value": "working"},
        {"from": "tool", "value": "obs"},
    ], traj)
    kinds = [s.type for s in traj.steps]
    assert "assistant_message" in kinds and "user_message" in kinds


def test_build_comparison_table(tmp_path):
    for agent, succ, score in [("hermes", True, 1.0), ("claude_sdk", False, 0.4)]:
        d = tmp_path / f"tau_bench__{agent}"
        d.mkdir(parents=True)
        (d / "results.jsonl").write_text(
            json.dumps({"task_id": "t1", "trial": 0, "success": succ,
                        "score": score, "metrics": {"domain": "retail"}}) + "\n",
            encoding="utf-8")
    comp = build_comparison("tau_bench", ["hermes", "claude_sdk"], tmp_path)
    assert len(comp["rows"]) == 1
    assert comp["summary"]["hermes"]["pass_rate"] == 1.0
    assert comp["summary"]["claude_sdk"]["pass_rate"] == 0.0
    assert comp["summary"]["claude_sdk"]["mean_score"] == 0.4
