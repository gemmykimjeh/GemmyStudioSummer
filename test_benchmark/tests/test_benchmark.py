"""Unit tests for the Benchmark / Env interface and the registry."""

from __future__ import annotations

import pytest

from harness import registry
from harness.benchmark import Benchmark, Env
from harness.benchmarks._stub import StubBenchmark
from harness.schema import Result, ToolSpec


def test_benchmark_and_env_are_abstract():
    with pytest.raises(TypeError):
        Benchmark()  # type: ignore[abstract]
    with pytest.raises(TypeError):
        Env()  # type: ignore[abstract]


def test_registry_populates_expected_names():
    registry.load_builtins()
    agents = registry.available_agents()
    benches = registry.available_benchmarks()
    assert {"scripted", "claude_sdk", "hermes"} <= set(agents)
    # all 7 benchmarks registered (WildClawBench excluded — see docs/DEFERRED.md)
    assert {
        "tau_bench", "automation_bench", "osworld", "swe_bench",
        "terminal_bench", "browsecomp", "gdpval",
    } <= set(benches)
    assert "wildclaw" not in benches


def test_get_unknown_raises():
    registry.load_builtins()
    with pytest.raises(KeyError):
        registry.get_benchmark("does_not_exist")
    with pytest.raises(KeyError):
        registry.get_agent("does_not_exist")


def test_env_tools_are_toolspecs():
    registry.load_builtins()
    # real=False keeps this core interface test offline / upstream-independent.
    bench = registry.get_benchmark("tau_bench", real=False)
    task = bench.load_tasks(limit=1)[0]
    env = bench.setup(task)
    tools = env.tools()
    assert tools and all(isinstance(t, ToolSpec) for t in tools)
    # invalid tool call returns an error result, does not raise
    res = env.call_tool("no_such_tool", {})
    assert res.is_error


def test_no_stub_benchmarks_remain():
    # Every registered benchmark is now a real adapter (none left on StubBenchmark).
    registry.load_builtins()
    for name in registry.available_benchmarks():
        assert not isinstance(registry.get_benchmark(name), StubBenchmark), name


def test_stub_base_still_raises():
    # The StubBenchmark base is unused by real adapters but must still fail loudly.
    class _S(StubBenchmark):
        run_notes = "x"
    with pytest.raises(NotImplementedError):
        _S().load_tasks()


def test_score_returns_result():
    registry.load_builtins()
    # real=False keeps this core interface test offline / upstream-independent.
    bench = registry.get_benchmark("automation_bench", real=False)
    task = bench.load_tasks(limit=1)[0]
    env = bench.setup(task)
    from harness.agents.scripted import ScriptedAgent
    traj = ScriptedAgent().run(task, env)
    result = bench.score(task, traj, env)
    assert isinstance(result, Result)
    assert result.benchmark == "automation_bench"
