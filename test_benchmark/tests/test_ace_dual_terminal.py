"""Tests for ace_dual_terminal (Terminal-Bench 2.0 + feedback-loop ACE).

Offline: no ace repo, no Anthropic key, no Docker. We cover registration, the
ACE_PATH default (this arm MUST point at the feedback-loop repo, not baseline),
the shell loop, and the CITED: parsing that feeds ACE's helpful/harmful counting.
The `_single_learn` call itself needs the real ACE object and is exercised
manually in a live run.
"""

from __future__ import annotations

import pytest

from harness import registry
from harness.agents import ace_dual_terminal as adt
from harness.schema import Task, Trajectory

from tests.test_ace_terminal import FakeEnv, FakeClient


def _agent(**kw) -> adt.ACEDualTerminalAgent:
    a = adt.ACEDualTerminalAgent(**kw)
    a._ready = True
    a._client = FakeClient()
    return a


def test_registered_as_agent():
    registry.load_builtins()
    assert isinstance(registry.get_agent("ace_dual_terminal"), adt.ACEDualTerminalAgent)


def test_defaults_to_the_feedback_loop_repo(monkeypatch):
    """The A/B contract: ace_terminal -> baseline ReAct, this -> ReAct_feedback_loop."""
    monkeypatch.delenv("ACE_PATH", raising=False)
    assert _agent().ace_path.endswith("ReAct_feedback_loop")


def test_ace_path_env_override(monkeypatch):
    monkeypatch.setenv("ACE_PATH", r"C:\somewhere\else")
    assert _agent().ace_path.endswith("else")


def test_rejects_unknown_feedback_mode():
    with pytest.raises(ValueError, match="feedback must be"):
        adt.ACEDualTerminalAgent(feedback="bogus")


def test_shell_loop_drives_tools_and_is_generic():
    agent, env = _agent(), FakeEnv()
    task = Task(id="fix-git", benchmark="terminal_bench", prompt="Fix the repo.")
    traj = Trajectory()
    summary, trace, cited = agent._solve(task, env, traj, shown_pb="")

    assert env.calls == [("bash", {"command": "git status"})]
    assert summary == "Done: repaired the index."
    assert "git status" in trace
    assert cited == []  # the fake client emits no CITED: line


def test_playbook_view_is_injected_into_the_system_prompt():
    """The learned playbook + immutable rulebook must reach the model."""
    agent, env = _agent(), FakeEnv()
    seen = {}
    real_create = agent._client.create

    def spy(**kw):
        seen.setdefault("system", kw.get("system", ""))
        return real_create(**kw)

    agent._client.create = spy
    agent._solve(Task(id="t", benchmark="terminal_bench", prompt="p"), env,
                 Trajectory(), shown_pb="## RULEBOOK — immutable\n[rul-00001] always verify")
    assert "[rul-00001] always verify" in seen["system"]
    assert "CITED:" in seen["system"]  # the citation contract is stated


def test_cited_uses_the_cited_line_when_present():
    got = _agent()._cited("done.\nCITED: [abc-00001] [def-00002]",
                          shown_pb="", trace="")
    assert got == ["abc-00001", "def-00002"]


@pytest.mark.parametrize("text", ["done.\nCITED: none", "no citation line", None])
def test_cited_falls_back_to_the_shared_helper(text, monkeypatch):
    """A weak model self-reports 'none' even when it demonstrably applied a
    bullet, so an empty CITED: must fall through to the same focused-question
    helper the baseline arm uses — otherwise the A/B penalises this arm purely
    on citation rate, and ACE's helpful/harmful counting silently stops."""
    called = {}

    def fake(client, model, playbook, trace, **kw):
        called["args"] = (playbook, trace)
        return ["fmt-00007"]

    monkeypatch.setattr("harness.agents._tb_verifier.cite_bullets", fake)
    got = _agent()._cited(text, shown_pb="[fmt-00007] x", trace="did a thing")
    assert got == ["fmt-00007"]
    assert called["args"] == ("[fmt-00007] x", "did a thing")


def test_both_arms_share_one_citation_implementation():
    """A/B fairness invariant: if the arms ever diverge on citation again, the
    citation rate becomes a property of the arm instead of the harness."""
    import inspect
    from harness.agents import _tb_verifier
    from harness.agents import ace_terminal as at
    assert callable(_tb_verifier.cite_bullets)
    for src in (inspect.getsource(at.ACETerminalAgent._cite_bullets),
                inspect.getsource(adt.ACEDualTerminalAgent._cited)):
        assert "cite_bullets" in src


def test_default_rulebook_is_the_shell_one_and_exists():
    """The repo's packaged rulebook is document-shaped and would tell a shell
    agent to write a document; this arm must inject the terminal one."""
    from pathlib import Path
    rb = Path(_agent().rulebook_path)
    assert rb.name == "rulebook_terminal.txt"
    assert rb.is_file(), f"terminal rulebook missing at {rb}"
    text = rb.read_text(encoding="utf-8")
    assert "[rb-fmt-01]" in text
    # The whole file is fed to the model verbatim (comments included), so it
    # must not carry document-deliverable examples into a shell context.
    assert "wb.save" not in text and "xlsx" not in text


def test_rulebook_path_env_override(monkeypatch):
    monkeypatch.setenv("ACE_TB_RULEBOOK", r"C:\custom\rb.txt")
    assert _agent().rulebook_path == r"C:\custom\rb.txt"


def test_cited_ignores_ids_outside_the_cited_line():
    """Ids merely quoted mid-transcript are not claims of use — counting them
    would poison ACE's helpful/harmful weighting."""
    text = ("I considered [xyz-00009] but discarded it.\n"
            "CITED: [abc-00001]")
    assert _agent()._cited(text, shown_pb="", trace="") == ["abc-00001"]
