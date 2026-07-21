"""Tests for the GDPval adapter (openai/gdpval, not GDPval-AA).

Offline (always run): rubric parsing/scoring, registry wiring, the missing-key
precheck, the empty-deliverable path, and end-to-end scoring with a mocked judge.
Task loading runs when `datasets` + the HF dataset are available. A real scored
run spends API (agent deliverable + rubric judge) and is exercised manually.
"""

from __future__ import annotations

import importlib.util

import pytest

from harness import registry
from harness.benchmarks import gdpval as gd
from harness.benchmarks._stub import StubBenchmark
from harness.schema import Task, Trajectory


def test_registered_as_real_benchmark():
    registry.load_builtins()
    b = registry.get_benchmark("gdpval")
    assert isinstance(b, gd.GDPval)
    assert not isinstance(b, StubBenchmark)


def test_parse_grades_robust():
    text = ('Here are the grades:\n'
            '[{"rubric_item_id": "a", "met": true}, '
            '{"rubric_item_id": "b", "met": false}]\nDone.')
    grades = gd._parse_grades(text)
    assert grades == {"a": True, "b": False}
    assert gd._parse_grades("no json here") == {}


def test_score_rubric_weighted_and_clamped():
    rubric = [
        {"rubric_item_id": "a", "score": 2},
        {"rubric_item_id": "b", "score": 2},
        {"rubric_item_id": "c", "score": 1, "required": True},
    ]
    # met a only -> 2 / 5 max positive
    rs = gd._score_rubric(rubric, {"a": True, "b": False, "c": False})
    assert rs["points_max"] == 5 and rs["points_earned"] == 2
    assert rs["score"] == pytest.approx(2 / 5)
    assert rs["n_met"] == 1
    assert rs["required_ok"] is False  # required item "c" not met
    # a penalty criterion cannot push score below 0
    pen = [{"rubric_item_id": "p", "score": -3}, {"rubric_item_id": "q", "score": 1}]
    assert gd._score_rubric(pen, {"p": True, "q": False})["score"] == 0.0


def test_setup_requires_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    task = Task(id="t", benchmark="gdpval", prompt="p", metadata={"rubric": []})
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        gd.GDPval().setup(task)


def test_score_empty_deliverable_no_api():
    # empty deliverable short-circuits before any judge call
    task = Task(id="t", benchmark="gdpval", prompt="p",
                metadata={"rubric": [{"rubric_item_id": "a", "score": 2}],
                          "occupation": "Auditors", "sector": "X"})
    r = gd.GDPval().score(task, Trajectory(final_output="   "), gd.GDPvalEnv(task))
    assert r.success is False and r.metrics["empty_deliverable"] is True


def test_score_with_mocked_judge(monkeypatch):
    class _Block:
        type = "text"
        text = ('[{"rubric_item_id": "a", "met": true}, '
                '{"rubric_item_id": "b", "met": false}]')

    class _Resp:
        content = [_Block()]

    class _Msgs:
        def create(self, **kwargs):
            return _Resp()

    class _Client:
        messages = _Msgs()

    monkeypatch.setattr(gd, "_anthropic", lambda: _Client())
    rubric = [{"rubric_item_id": "a", "score": 2, "criterion": "x"},
              {"rubric_item_id": "b", "score": 2, "criterion": "y"}]
    task = Task(id="t", benchmark="gdpval", prompt="do the thing",
                metadata={"rubric": rubric, "occupation": "Auditors", "sector": "X"})
    r = gd.GDPval().score(task, Trajectory(final_output="my deliverable"),
                          gd.GDPvalEnv(task))
    assert r.score == pytest.approx(0.5)      # 2 of 4 points
    assert r.success is True                  # 0.5 >= threshold, no required items
    assert r.metrics["n_met"] == 1 and r.metrics["points_max"] == 4


class _RetryClient:
    """A fake judge whose reply improves on retry, to exercise grade_with_retry."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = 0
        self.temps = []

        class _Msgs:
            def create(inner, **kw):  # noqa: N805
                self.temps.append(kw.get("temperature"))
                text = self._replies[min(self.calls, len(self._replies) - 1)]
                self.calls += 1
                return type("R", (), {"content": [type("B", (), {"type": "text", "text": text})()]})()
        self.messages = _Msgs()


def test_grade_with_retry_recovers_from_malformed_first_reply():
    good = '[{"rubric_item_id": "a", "met": true}, {"rubric_item_id": "b", "met": true}]'
    client = _RetryClient(["garbage, not json at all", good])
    grades = gd.grade_with_retry(client, "m", 4096, "prompt", n_criteria=2)
    assert grades == {"a": True, "b": True}
    assert client.calls == 2                 # retried once
    assert client.temps[0] == 0.0            # deterministic first attempt
    assert client.temps[1] == 0.5            # jitter on retry


def test_grade_with_retry_rejects_partial_parse():
    # Only 1 of 4 criteria parsed on every attempt -> below the half threshold,
    # so all 3 attempts are consumed and the last (partial) verdict is returned.
    partial = '[{"rubric_item_id": "a", "met": true}]'
    client = _RetryClient([partial])
    grades = gd.grade_with_retry(client, "m", 4096, "prompt", n_criteria=4, tries=3)
    assert client.calls == 3                 # never satisfied the >=2 threshold
    assert grades == {"a": True}


def test_submission_for_grader_framing():
    none = gd.submission_for_grader("x", {"fail_type": "none", "ok": True,
                                          "files": ["out.xlsx"], "extracted": "DATA"})
    assert "ACTUAL deliverable files" in none and "out.xlsx" in none and "DATA" in none
    model = gd.submission_for_grader("prose", {"fail_type": "model", "ok": False, "files": []})
    assert "No deliverable file exists" in model and model.endswith("prose")
    env = gd.submission_for_grader("prose", {"fail_type": "env", "ok": False, "files": []})
    assert "could not execute" in env
    nocode = gd.submission_for_grader("just text", {"fail_type": "no_code", "ok": False})
    assert nocode == "just text"


@pytest.mark.skipif(importlib.util.find_spec("datasets") is None,
                    reason="datasets not installed")
def test_load_tasks_reads_gdpval():
    try:
        tasks = gd.GDPval().load_tasks(limit=3)
    except Exception as exc:  # noqa: BLE001 - offline / HF unreachable
        pytest.skip(f"openai/gdpval not available: {exc}")
    assert 1 <= len(tasks) <= 3
    for t in tasks:
        assert t.benchmark == "gdpval"
        assert t.prompt.strip()
        assert t.metadata["rubric"]                 # official rubric criteria present
        assert t.metadata["occupation"] and t.metadata["sector"]
