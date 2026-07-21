"""Tests for the ace_terminal agent (Terminal-Bench 2.0 + ACE playbook).

Everything here is offline: no ace repo, no Anthropic key, no Docker. We cover
the parts that don't need the ACE brain — registration, config validation, the
generic tool loop (against a fake Env + fake client), and the reward-signal
degradation paths. A live scored run needs Docker + a key and is done manually.
"""

from __future__ import annotations

import pytest

from harness import registry
from harness.agents import ace_terminal as at
from harness.schema import Task, ToolResult, ToolSpec


# ----------------------------------------------------------------- fakes
class FakeEnv:
    """Minimal Env-shaped stub exposing a single `bash` tool, like terminal_bench."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def observation(self) -> str:
        return "Fix the broken git repo at /app."

    def instructions(self) -> str:
        return "You are working inside a Linux container via the `bash` tool."

    def tools(self) -> list[ToolSpec]:
        return [ToolSpec(name="bash", description="Run a shell command.",
                         parameters={"type": "object",
                                     "properties": {"command": {"type": "string"}},
                                     "required": ["command"]})]

    def call_tool(self, name: str, arguments: dict) -> ToolResult:
        self.calls.append((name, arguments))
        return ToolResult(output=f"ran {arguments.get('command')!r}", is_error=False)

    def snapshot(self) -> dict:
        return {"commands": len(self.calls)}


class _Block:
    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


class _Usage:
    input_tokens = 100
    output_tokens = 50


class _Response:
    def __init__(self, content, stop_reason) -> None:
        self.content = content
        self.stop_reason = stop_reason
        self.usage = _Usage()


class FakeClient:
    """Emits one tool_use turn, then a final text turn."""

    def __init__(self) -> None:
        self.messages = self
        self._turn = 0

    def create(self, **kw):  # noqa: ARG002 - signature-compatible stub
        self._turn += 1
        if self._turn == 1:
            return _Response(
                [_Block(type="text", text="Let me inspect the repo."),
                 _Block(type="tool_use", id="tu_1", name="bash",
                        input={"command": "git status"})],
                stop_reason="tool_use")
        return _Response([_Block(type="text", text="Done: repaired the index.")],
                         stop_reason="end_turn")


def _agent(**kw) -> at.ACETerminalAgent:
    """An agent with the ACE brain pre-stubbed so _ensure() never runs."""
    a = at.ACETerminalAgent(**kw)
    a._ready = True
    a._client = FakeClient()
    return a


# ----------------------------------------------------------------- tests
def test_registered_as_agent():
    registry.load_builtins()
    assert isinstance(registry.get_agent("ace_terminal"), at.ACETerminalAgent)


def test_ace_react_name_is_gone_and_browsecomp_registered():
    """The rename must not leave the old name resolvable."""
    registry.load_builtins()
    assert "ace_browsecomp" in registry.available_agents()
    assert "ace_react" not in registry.available_agents()


def test_rejects_unknown_feedback_mode():
    with pytest.raises(ValueError, match="feedback must be"):
        at.ACETerminalAgent(feedback="bogus")


@pytest.mark.parametrize("mode", ["shadow", "none", "inplace"])
def test_accepts_documented_feedback_modes(mode):
    assert at.ACETerminalAgent(feedback=mode).feedback == mode


def test_ace_path_defaults_to_baseline_and_honours_env(monkeypatch):
    monkeypatch.delenv("ACE_PATH", raising=False)
    assert at.ACETerminalAgent().ace_path.endswith("ReAct")
    monkeypatch.setenv("ACE_PATH", r"C:\GemmyStudioSummer\ReAct_feedback_loop")
    assert at.ACETerminalAgent().ace_path.endswith("ReAct_feedback_loop")


def test_tool_loop_is_generic_and_drives_bash():
    """The loop must come from env.tools()/call_tool() with no hardcoded names."""
    agent, env = _agent(), FakeEnv()
    task = Task(id="fix-git", benchmark="terminal_bench", prompt="Fix the repo.")
    from harness.schema import Trajectory
    traj = Trajectory()
    summary, trace = agent._solve(task, env, traj)

    assert env.calls == [("bash", {"command": "git status"})]
    assert summary == "Done: repaired the index."
    assert "git status" in trace
    assert traj.tokens == 300  # two turns x 150
    assert [s.type for s in traj.steps] == [
        "user_message", "assistant_message", "tool_call", "assistant_message"]


def test_reward_signal_skipped_when_feedback_none():
    v = _agent(feedback="none")._reward_signal(
        Task(id="t", benchmark="terminal_bench", prompt="p"), FakeEnv())
    assert v.reward is None and v.solved is None and "feedback='none'" in v.note


def test_reward_signal_degrades_on_non_terminal_env():
    """ace_terminal is generic; on another benchmark it skips the TB2 verifier
    instead of raising."""
    v = _agent(feedback="shadow")._reward_signal(
        Task(id="t", benchmark="other", prompt="p"), FakeEnv())
    assert v.reward is None and "not a TerminalBenchEnv" in v.note


def test_reward_signal_reports_missing_tests(tmp_path):
    """A TerminalBenchEnv whose task_dir has no tests/ yields a reason, not a crash."""
    from harness.benchmarks import terminal_bench as tb
    task = Task(id="t", benchmark="terminal_bench", prompt="p",
                metadata={"task_dir": str(tmp_path), "docker_image": "img"})
    env = tb.TerminalBenchEnv(task, "img", "container", None)
    v = _agent(feedback="shadow")._reward_signal(task, env)
    assert v.reward is None and "no tests/test.sh" in v.note


# ------------------------------------------------- shared verifier result
def test_verifier_summary_without_verdict_does_not_assert_pass_or_fail():
    """No reward must not become a silent 'FAILED' — that would teach the
    playbook from a verdict we never obtained."""
    from harness.agents._tb_verifier import VerifierResult
    text = VerifierResult(note="docker commit failed").summary()
    assert "No verifier result" in text
    assert "PASSED" not in text and "FAILED" not in text


def test_verifier_summary_lists_failing_tests():
    """Failing test names are the terminal analogue of GDPval's missed rubric
    criteria — they must reach the Reflector."""
    from harness.agents._tb_verifier import VerifierResult
    text = VerifierResult(reward=0.0, note="official verifier, shadow mode",
                          failed_tests=["test_outputs.py::test_index_repaired"],
                          tests_total=3, tests_passed=2).summary()
    assert "FAILED" in text
    assert "2/3 tests passed" in text
    assert "test_index_repaired" in text


def test_verifier_solved_flag():
    from harness.agents._tb_verifier import VerifierResult
    assert VerifierResult(reward=1.0).solved is True
    assert VerifierResult(reward=0.0).solved is False
    assert VerifierResult().solved is None
