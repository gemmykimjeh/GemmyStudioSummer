"""End-to-end tests of the ACE training step with the model calls faked.

These cover the ordering and bookkeeping of ``ACEGAIAEvaluation.evaluate_instance``
— the parts that only show up when the whole step runs: which answer becomes the
reported score, when reflection rounds fire, what the Curator's output does to
the playbook, and what happens to an instance that never produced an answer.
"""

import json

import pytest

from ace_gaia.runner import ACEGAIAEvaluation
from benchmarks.gaia.run_infer import GAIAEvaluation
from benchmarks.utils.models import EvalInstance, EvalMetadata, EvalOutput
from openhands.sdk import LLM
from openhands.sdk.critic import PassCritic


REFLECTOR_REPLY = json.dumps(
    {
        "reasoning": "The agent stopped at the first search result.",
        "error_identification": "Did not check the second attachment.",
        "root_cause_analysis": "Assumed a single input file.",
        "correct_approach": "Enumerate all attachments before answering.",
        "key_insight": "Enumerate every attachment before answering.",
        "bullet_tags": [{"id": "err-00001", "tag": "helpful"}],
    }
)

CURATOR_REPLY = json.dumps(
    {
        "reasoning": "This lesson is not yet in the playbook.",
        "operations": [
            {
                "type": "ADD",
                "section": "common_mistakes_to_avoid",
                "content": "Enumerate every attachment before answering.",
            }
        ],
    }
)


def _metadata(tmp_path):
    return EvalMetadata(
        llm=LLM(
            model="anthropic/claude-sonnet-4-5-20250929",
            api_key="sk-test",
            temperature=0.0,
            usage_id="test",
        ),
        dataset="gaia-benchmark/GAIA",
        dataset_split="validation",
        max_iterations=500,
        eval_output_dir=str(tmp_path),
        details={"level": "2023_all"},
        critic=PassCritic(),
    )


def _instance():
    return EvalInstance(
        id="task-1",
        data={
            "Question": "How many rows are in the attached sheet?",
            "Final answer": "42",
            "file_name": "sheet.xlsx",
        },
    )


def _output(answer, correct, error=None, raw=None):
    return EvalOutput(
        instance_id="task-1",
        attempt=1,
        test_result={
            "score": correct,
            "model_answer": answer,
            "model_answer_raw": raw
            if raw is not None
            else f"<solution>{answer}</solution> "
            f"<bullets_used>err-00001</bullets_used>",
            "ground_truth": "42",
        },
        instruction="...",
        history=[],
        error=error,
    )


@pytest.fixture
def patched(tmp_path, monkeypatch):
    """An evaluator whose agent runs and LLM calls are canned."""
    from ace_gaia.vendor.ace.core import (
        curator as curator_mod,
        reflector as reflector_mod,
    )

    calls = {"agent_runs": 0, "reflect": 0, "curate": 0}

    def fake_reflector_call(*args, **kwargs):
        calls["reflect"] += 1
        return REFLECTOR_REPLY, {"role": "reflector", "call_id": "x"}

    def fake_curator_call(*args, **kwargs):
        calls["curate"] += 1
        return CURATOR_REPLY, {"role": "curator", "call_id": "x"}

    monkeypatch.setattr(reflector_mod, "timed_llm_call", fake_reflector_call)
    monkeypatch.setattr(curator_mod, "timed_llm_call", fake_curator_call)

    evaluator = ACEGAIAEvaluation(metadata=_metadata(tmp_path), num_workers=1)
    evaluator._total_samples = 1
    return evaluator, calls, monkeypatch


def _stub_agent_runs(monkeypatch, calls, outputs):
    """Replace the agent run with a fixed sequence of outputs, one per round."""

    def fake_evaluate(self, instance, workspace):
        index = calls["agent_runs"]
        calls["agent_runs"] += 1
        return outputs[min(index, len(outputs) - 1)]

    monkeypatch.setattr(GAIAEvaluation, "evaluate_instance", fake_evaluate)


# ------------------------------------------------------------------- tests


def test_reported_score_is_pre_train_not_post_train(patched):
    """The headline score must be the attempt made before ground truth was seen.

    Reflection rounds retry until correct using the answer key, so scoring the
    post-train answer would measure the retry loop, not the playbook.
    """
    evaluator, calls, monkeypatch = patched
    _stub_agent_runs(
        monkeypatch, calls, [_output("41", False), _output("42", True)]
    )

    out = evaluator.evaluate_instance(_instance(), workspace=None)

    assert out.test_result["score"] is False
    assert out.test_result["ace_pre_train_answer"] == "41"
    assert out.test_result["ace_pre_train_correct"] is False
    assert out.test_result["ace_post_train_answer"] == "42"
    assert out.test_result["ace_post_train_correct"] is True
    assert out.test_result["ace_reflection_rounds"] == 1


def test_correct_answer_skips_rounds_but_still_reflects(patched):
    """ACE reflects on successes too, to tag the bullets that were cited."""
    evaluator, calls, monkeypatch = patched
    _stub_agent_runs(monkeypatch, calls, [_output("42", True)])

    out = evaluator.evaluate_instance(_instance(), workspace=None)

    assert out.test_result["score"] is True
    assert out.test_result["ace_reflection_rounds"] == 0
    assert calls["agent_runs"] == 1
    assert calls["reflect"] == 1


def test_rounds_are_bounded(patched):
    """A never-correct task stops at max_num_rounds instead of looping."""
    evaluator, calls, monkeypatch = patched
    evaluator.ace_config["max_num_rounds"] = 3
    _stub_agent_runs(monkeypatch, calls, [_output("41", False)])

    out = evaluator.evaluate_instance(_instance(), workspace=None)

    assert out.test_result["ace_reflection_rounds"] == 3
    assert calls["agent_runs"] == 4  # 1 initial + 3 rounds
    assert calls["reflect"] == 3


def test_curator_output_lands_in_the_playbook(patched):
    evaluator, calls, monkeypatch = patched
    _stub_agent_runs(monkeypatch, calls, [_output("42", True)])

    evaluator.evaluate_instance(_instance(), workspace=None)

    assert calls["curate"] == 1
    assert "Enumerate every attachment before answering." in evaluator._playbook
    assert "[err-00001]" in evaluator._playbook


def test_bullet_counts_update_from_reflector_tags(patched):
    """The second instance sees the counts earned on the first."""
    evaluator, calls, monkeypatch = patched
    _stub_agent_runs(monkeypatch, calls, [_output("42", True)])

    evaluator.evaluate_instance(_instance(), workspace=None)
    assert "[err-00001] helpful=0 harmful=0" in evaluator._playbook

    evaluator.evaluate_instance(_instance(), workspace=None)
    assert "[err-00001] helpful=1 harmful=0" in evaluator._playbook


def test_playbook_is_injected_into_the_next_instance(patched):
    """What the Curator wrote on step 1 must reach the agent on step 2."""
    evaluator, calls, monkeypatch = patched
    _stub_agent_runs(monkeypatch, calls, [_output("42", True)])

    evaluator.evaluate_instance(_instance(), workspace=None)
    evaluator.evaluate_instance(_instance(), workspace=None)

    playbook_shown, _ = evaluator._injection
    assert "Enumerate every attachment before answering." in playbook_shown


def test_missing_tag_is_recorded_not_silently_empty(patched):
    """A dropped tag must be visible in the output, not read as 'used nothing'."""
    evaluator, calls, monkeypatch = patched
    _stub_agent_runs(
        monkeypatch, calls, [_output("42", True, raw="<solution>42</solution>")]
    )

    out = evaluator.evaluate_instance(_instance(), workspace=None)

    assert out.test_result["ace_bullet_ids_used"] == []
    assert out.test_result["ace_bullets_reported"] is False


def test_reported_empty_tag_is_recorded_as_compliant(patched):
    evaluator, calls, monkeypatch = patched
    _stub_agent_runs(
        monkeypatch,
        calls,
        [_output("42", True, raw="<solution>42</solution> <bullets_used></bullets_used>")],
    )

    out = evaluator.evaluate_instance(_instance(), workspace=None)

    assert out.test_result["ace_bullet_ids_used"] == []
    assert out.test_result["ace_bullets_reported"] is True


def test_errored_instance_is_not_an_ace_sample(patched):
    """ACE has no error path; a failed instance must not train the playbook.

    Otherwise the Reflector diagnoses infrastructure noise and the Curator
    distils a bullet from it, which then sits in every later task's context.
    """
    evaluator, calls, monkeypatch = patched
    _stub_agent_runs(
        monkeypatch, calls, [_output("", False, error="workspace start failed")]
    )
    before = evaluator._playbook

    out = evaluator.evaluate_instance(_instance(), workspace=None)

    assert out.test_result["ace_skipped"] == "instance_error"
    assert calls["reflect"] == 0
    assert calls["curate"] == 0
    assert evaluator._playbook == before


def test_step_state_is_persisted(patched, tmp_path):
    evaluator, calls, monkeypatch = patched
    _stub_agent_runs(monkeypatch, calls, [_output("42", True)])

    evaluator.evaluate_instance(_instance(), workspace=None)

    playbooks = tmp_path / "ace" / "playbooks"
    assert (playbooks / "playbook_latest.md").exists()
    assert list(playbooks.glob("playbook_step0001_*.md"))
    assert (tmp_path / "ace" / "rounds" / "task-1_round0.json").exists()
    assert (tmp_path / "ace" / "bullet_usage.jsonl").exists()


def test_resumes_from_a_saved_playbook(patched, tmp_path):
    """A restarted run picks the playbook back up instead of relearning."""
    evaluator, calls, monkeypatch = patched
    _stub_agent_runs(monkeypatch, calls, [_output("42", True)])
    evaluator.evaluate_instance(_instance(), workspace=None)

    resumed = ACEGAIAEvaluation(metadata=_metadata(tmp_path), num_workers=1)
    assert "Enumerate every attachment before answering." in resumed._playbook
