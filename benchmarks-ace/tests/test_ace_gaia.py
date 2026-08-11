"""Tests for the ACE/OpenHands integration.

Everything here runs without a model, a network or GAIA: the pieces covered are
the deterministic ones — trace compaction, bullet-ID extraction, context
rendering, the sequential-execution guard, and the vendored playbook mechanics.
"""

import pytest

from ace_gaia.runner import EMPTY_PLAYBOOK, ACEGAIAEvaluation
from ace_gaia.trace import compact_trace
from ace_gaia.vendor.playbook_utils import (
    apply_curator_operations,
    get_next_global_id,
    get_playbook_stats,
    update_bullet_counts,
)
from benchmarks.utils.models import EvalMetadata
from openhands.sdk import LLM, TextContent
from openhands.sdk.critic import PassCritic


# ----------------------------------------------------------------- fixtures


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


@pytest.fixture
def evaluator(tmp_path):
    return ACEGAIAEvaluation(metadata=_metadata(tmp_path), num_workers=1)


# Minimal stand-ins: compact_trace dispatches on class name, so the names here
# are the contract, not the fields.


class ActionEvent:
    def __init__(self, thought, tool_name, arguments):
        self.thought = [TextContent(text=thought)]
        self.tool_name = tool_name
        self.tool_call = type("ToolCall", (), {"arguments": arguments})()
        self.action = None
        self.source = "agent"


class ObservationEvent:
    def __init__(self, text):
        self.observation = type("Obs", (), {"text": text})()
        self.source = "environment"


class MessageEvent:
    def __init__(self, text, source="agent"):
        self.llm_message = type("Msg", (), {"content": [TextContent(text=text)]})()
        self.source = source


class SystemPromptEvent:
    def __init__(self):
        self.source = "agent"


def _long_trace(n_steps=60):
    events = [SystemPromptEvent()]
    for i in range(n_steps):
        events.append(
            ActionEvent(f"thinking about step {i}", "execute_bash", '{"command":"ls"}')
        )
        events.append(ObservationEvent(f"output for step {i} " + "x" * 400))
    events.append(MessageEvent("<solution>42</solution>"))
    return events


# ------------------------------------------------------------ trace compaction


def test_compact_trace_empty():
    assert compact_trace([]) == "(no diagnostic events in trace)"


def test_compact_trace_skips_non_diagnostic_events():
    assert compact_trace([SystemPromptEvent()]) == "(no diagnostic events in trace)"


def test_compact_trace_renders_actions_and_observations():
    trace = compact_trace([ActionEvent("plan it", "execute_bash", '{"command":"ls"}')])
    assert "THOUGHT: plan it" in trace
    assert "ACTION: execute_bash" in trace


def test_compact_trace_respects_budget_and_marks_elision():
    trace = compact_trace(_long_trace(), max_chars=2000)
    assert len(trace) <= 2200  # budget plus the elision marker
    assert "steps omitted" in trace


def test_compact_trace_keeps_head_and_tail():
    """The opening carries the plan; the end carries the failure and the answer."""
    trace = compact_trace(_long_trace(), max_chars=2000)
    assert "thinking about step 0" in trace
    assert "<solution>42</solution>" in trace


def test_compact_trace_is_verbatim():
    """Every rendered fragment must appear literally in the source events.

    The Reflector diagnoses what it is shown, so the trace has to be a record of
    the run rather than a paraphrase of it.
    """
    events = [ActionEvent("distinctive thought text", "browser", '{"url":"x"}')]
    trace = compact_trace(events)
    assert "distinctive thought text" in trace


def test_compact_trace_is_deterministic():
    events = _long_trace()
    assert compact_trace(events, 2000) == compact_trace(events, 2000)


def test_compact_trace_truncates_oversized_observations():
    trace = compact_trace([ObservationEvent("y" * 5000)])
    assert "[+" in trace
    assert len(trace) < 1200


# ------------------------------------------------------- bullet-ID extraction


def _with_bullet(evaluator, section="common_mistakes_to_avoid", content="Check twice."):
    ops = [{"type": "ADD", "section": section, "content": content}]
    evaluator._playbook, _ = apply_curator_operations(
        evaluator._playbook, ops, get_next_global_id(evaluator._playbook)
    )
    return evaluator


def test_extract_bullet_ids_from_tag(evaluator):
    _with_bullet(evaluator)
    ids, reported = evaluator._extract_bullet_ids(
        "<solution>42</solution> <bullets_used>err-00001</bullets_used>"
    )
    assert ids == ["err-00001"]
    assert reported is True


def test_extract_bullet_ids_falls_back_to_bracket_scan(evaluator):
    """A missing tag degrades to ACE's own regex rather than to a failure."""
    _with_bullet(evaluator)
    ids, _ = evaluator._extract_bullet_ids("I applied [err-00001] here")
    assert ids == ["err-00001"]


def test_extract_bullet_ids_drops_ids_not_in_playbook(evaluator):
    """A hallucinated ID must not be credited to a real bullet."""
    _with_bullet(evaluator)
    ids, _ = evaluator._extract_bullet_ids(
        "<bullets_used>err-00001, zzz-00099</bullets_used>"
    )
    assert ids == ["err-00001"]


def test_extract_bullet_ids_deduplicates(evaluator):
    _with_bullet(evaluator)
    ids, _ = evaluator._extract_bullet_ids(
        "<bullets_used>err-00001, err-00001</bullets_used>"
    )
    assert ids == ["err-00001"]


def test_empty_tag_is_distinguished_from_missing_tag(evaluator):
    """Both cite nothing, but only one of them answered the question.

    Conflating them is what let the first smoke run look healthy while the
    helpful/harmful counters were frozen at zero.
    """
    _with_bullet(evaluator)

    ids, reported = evaluator._extract_bullet_ids(
        "<solution>42</solution> <bullets_used></bullets_used>"
    )
    assert ids == [] and reported is True  # agent said: I used none

    ids, reported = evaluator._extract_bullet_ids("<solution>42</solution>")
    assert ids == [] and reported is False  # agent never answered

    ids, reported = evaluator._extract_bullet_ids("")
    assert ids == [] and reported is False


# --------------------------------------------------------- context injection


def test_render_suffix_includes_playbook(evaluator):
    _with_bullet(evaluator, content="Check for a second attachment.")
    suffix = evaluator._render_suffix(evaluator._playbook, "(empty)")
    assert "Check for a second attachment." in suffix
    assert "err-00001" in suffix
    assert "helpful=0 harmful=0" in suffix  # counts stay visible, as in ACE
    assert "Reflection on your previous attempt" not in suffix


def test_render_suffix_includes_reflection_when_present(evaluator):
    suffix = evaluator._render_suffix(evaluator._playbook, "You misread the units.")
    assert "You misread the units." in suffix
    assert "Reflection on your previous attempt" in suffix


def test_instruction_requests_bullet_report(evaluator):
    instance = type("I", (), {"data": {"Question": "What is 2+2?", "file_name": ""}})()
    instruction = evaluator._build_instruction(instance)
    assert "<bullets_used>" in instruction
    # The GAIA answer contract must survive the addition.
    assert "<solution>" in instruction


def test_bullet_report_is_requested_beside_the_playbook_too(evaluator):
    """Stated in both places: ACE enforces this via its output schema, we can't.

    The first smoke run dropped the tag on the one instance where the playbook
    was non-empty, with the request only at the tail of GAIA's instruction.
    """
    suffix = evaluator._render_suffix(evaluator._playbook, "(empty)")
    assert "<bullets_used>" in suffix


# --------------------------------------------------------- sequential guard


def test_rejects_concurrent_workers(tmp_path):
    """Concurrency would let instances train against inconsistent playbooks."""
    with pytest.raises(ValueError, match="num_workers"):
        ACEGAIAEvaluation(metadata=_metadata(tmp_path), num_workers=4)


def test_accepts_single_worker(tmp_path):
    assert ACEGAIAEvaluation(metadata=_metadata(tmp_path), num_workers=1).num_workers == 1


# ----------------------------------------------------- vendored ACE mechanics


def test_empty_playbook_has_ace_sections():
    """Section names drive bullet-ID slugs, so they must match the reference."""
    for header in ("## COMMON MISTAKES TO AVOID", "## CONTEXT CLUES & INDICATORS"):
        assert header in EMPTY_PLAYBOOK
    assert get_playbook_stats(EMPTY_PLAYBOOK)["total_bullets"] == 0


def test_curator_add_produces_sectioned_ids():
    ops = [
        {"type": "ADD", "section": "common_mistakes_to_avoid", "content": "First."},
        {"type": "ADD", "section": "context_clues_and_indicators", "content": "Second."},
    ]
    playbook, next_id = apply_curator_operations(
        EMPTY_PLAYBOOK, ops, get_next_global_id(EMPTY_PLAYBOOK)
    )
    assert "[err-00001] helpful=0 harmful=0 :: First." in playbook
    assert "[ctx-00002] helpful=0 harmful=0 :: Second." in playbook
    assert next_id == 3
    assert get_playbook_stats(playbook)["total_bullets"] == 2


def test_bullet_counts_update_by_tag():
    ops = [{"type": "ADD", "section": "common_mistakes_to_avoid", "content": "First."}]
    playbook, _ = apply_curator_operations(
        EMPTY_PLAYBOOK, ops, get_next_global_id(EMPTY_PLAYBOOK)
    )
    playbook = update_bullet_counts(playbook, [{"id": "err-00001", "tag": "helpful"}])
    assert "[err-00001] helpful=1 harmful=0" in playbook
    playbook = update_bullet_counts(playbook, [{"id": "err-00001", "tag": "harmful"}])
    assert "[err-00001] helpful=1 harmful=1" in playbook
    # neutral is an explicit no-op in ACE
    playbook = update_bullet_counts(playbook, [{"id": "err-00001", "tag": "neutral"}])
    assert "[err-00001] helpful=1 harmful=1" in playbook


# ------------------------------------------------------------------ scoring


def test_data_processor_matches_gaia_scorer():
    from ace_gaia.data_processor import GAIADataProcessor

    dp = GAIADataProcessor()
    assert dp.answer_is_correct("42", "42")
    assert dp.answer_is_correct("1,234", "1234")  # comma stripped for numbers
    assert not dp.answer_is_correct("43", "42")
    assert not dp.answer_is_correct("", "42")
    assert dp.evaluate_accuracy(["42", "43"], ["42", "42"]) == 0.5
    assert dp.evaluate_accuracy([], []) == 0.0
