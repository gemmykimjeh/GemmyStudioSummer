"""Mechanical compaction of an OpenHands event log into ACE's ``reasoning_trace``.

ACE passes ``reasoning_trace=gen_response`` — the Generator's raw, verbatim
output (``ace/ace.py``). Its Generator is a single LLM call producing one JSON
blob, so the question of compaction never arose there. A GAIA run is up to 500
tool-calling iterations, so something has to reduce it.

That reduction is done here **mechanically**: every character in the output is
copied from an event, never paraphrased and never passed through a model. A
model-written summary would be cheaper to produce but would put the Reflector one
inference removed from what actually happened, and its diagnosis would be of the
summary rather than of the run.

Determinism matters too — no clock reads, no ordering by anything but event
order — so re-running the compactor over a persisted event log reproduces the
exact string the Reflector saw.
"""

from __future__ import annotations

import json
from typing import Any, Sequence


# Per-field truncation. Tool arguments and observations are the two unbounded
# fields; a single `str_replace_editor` view or a browser dump can otherwise
# swamp the entire budget on one step.
MAX_THOUGHT_CHARS = 600
MAX_ARGS_CHARS = 500
MAX_OBSERVATION_CHARS = 800

# Total budget for the assembled trace.
MAX_TRACE_CHARS = 10_000

# When over budget, keep this share of the budget for the opening of the run and
# spend the rest on the end. Failures and the final answer cluster at the tail,
# but the opening carries the agent's initial plan, which is where a strategy
# mistake is usually visible.
HEAD_SHARE = 0.3

ELISION = "\n\n... [{n} steps omitted] ...\n\n"


def _truncate(text: str, limit: int) -> str:
    """Cut to *limit* characters, marking the cut so the Reflector can see it."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f" ...[+{len(text) - limit} chars]"


def _text_of(content: Any) -> str:
    """Flatten a ``Sequence[TextContent]`` (or a bare string) to plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence):
        parts = []
        for item in content:
            text = getattr(item, "text", None)
            if text:
                parts.append(str(text))
        return "\n".join(parts)
    return str(getattr(content, "text", content) or "")


def _args_of(event: Any) -> str:
    """Best-effort extraction of a tool call's arguments as a compact string.

    Tries the structured ``action`` first (a pydantic model), then the raw
    ``tool_call.arguments``. Both are duck-typed: the tool set varies by run and
    a missing field must degrade to an empty string rather than raise inside the
    reflection path.
    """
    action = getattr(event, "action", None)
    if action is not None:
        dump = getattr(action, "model_dump", None)
        if callable(dump):
            try:
                return json.dumps(dump(mode="json"), ensure_ascii=False, sort_keys=True)
            except Exception:  # noqa: BLE001 - fall through to the raw form
                pass
        return str(action)

    tool_call = getattr(event, "tool_call", None)
    if tool_call is not None:
        args = getattr(tool_call, "arguments", None)
        if args is not None:
            return args if isinstance(args, str) else str(args)
    return ""


def _observation_of(event: Any) -> str:
    """Best-effort extraction of an observation's textual content."""
    observation = getattr(event, "observation", None)
    if observation is not None:
        for attr in ("text", "content", "output", "result"):
            value = getattr(observation, attr, None)
            if value:
                return _text_of(value)
        dump = getattr(observation, "model_dump", None)
        if callable(dump):
            try:
                return json.dumps(dump(mode="json"), ensure_ascii=False, sort_keys=True)
            except Exception:  # noqa: BLE001
                pass
        return str(observation)

    for attr in ("content", "text"):
        value = getattr(event, attr, None)
        if value:
            return _text_of(value)
    return ""


def _render(event: Any) -> str | None:
    """Render one event, or None if it carries nothing worth showing.

    Dispatch is by class name rather than isinstance so that a tool package
    registering its own event subclasses does not silently drop them.
    """
    kind = type(event).__name__
    source = getattr(event, "source", None)

    if kind == "ActionEvent":
        lines = []
        thought = _text_of(getattr(event, "thought", None))
        if thought:
            lines.append(f"THOUGHT: {_truncate(thought, MAX_THOUGHT_CHARS)}")
        tool_name = getattr(event, "tool_name", "unknown_tool")
        args = _args_of(event)
        lines.append(f"ACTION: {tool_name}({_truncate(args, MAX_ARGS_CHARS)})")
        return "\n".join(lines)

    if kind in ("ObservationEvent", "ObservationBaseEvent", "UserRejectObservation"):
        body = _observation_of(event)
        if not body:
            return None
        return f"OBSERVATION: {_truncate(body, MAX_OBSERVATION_CHARS)}"

    if kind == "AgentErrorEvent":
        body = _observation_of(event) or str(event)
        return f"ERROR: {_truncate(body, MAX_OBSERVATION_CHARS)}"

    if kind == "MessageEvent":
        message = getattr(event, "llm_message", None)
        body = _text_of(getattr(message, "content", None)) if message else ""
        if not body:
            return None
        who = "AGENT" if source == "agent" else "USER"
        return f"{who} MESSAGE: {_truncate(body, MAX_OBSERVATION_CHARS)}"

    if kind == "CondensationSummaryEvent":
        # The condenser is enabled for GAIA, so part of the run may already have
        # been replaced by an SDK-generated summary. Marking it keeps the
        # Reflector from reading condensed text as a verbatim record.
        body = _observation_of(event)
        if not body:
            return None
        return f"[CONDENSED HISTORY] {_truncate(body, MAX_OBSERVATION_CHARS)}"

    # SystemPromptEvent, ConversationStateUpdateEvent, PauseEvent, token/log
    # events: no diagnostic content.
    return None


def compact_trace(events: Sequence[Any], max_chars: int = MAX_TRACE_CHARS) -> str:
    """Render an event log to a bounded, verbatim-derived trace string.

    Args:
        events: ``conversation.state.events`` / ``EvalOutput.history``.
        max_chars: total budget for the returned string.

    Returns:
        The trace, head and tail preserved with an explicit elision marker in
        between when the full render exceeds the budget.
    """
    blocks: list[str] = []
    for index, event in enumerate(events):
        rendered = _render(event)
        if rendered:
            blocks.append(f"[{index}] {rendered}")

    if not blocks:
        return "(no diagnostic events in trace)"

    full = "\n\n".join(blocks)
    if len(full) <= max_chars:
        return full

    head_budget = int(max_chars * HEAD_SHARE)
    tail_budget = max_chars - head_budget

    head_blocks: list[str] = []
    used = 0
    for block in blocks:
        if used + len(block) > head_budget:
            break
        head_blocks.append(block)
        used += len(block) + 2

    tail_blocks: list[str] = []
    used = 0
    for block in reversed(blocks[len(head_blocks):]):
        if used + len(block) > tail_budget:
            break
        tail_blocks.append(block)
        used += len(block) + 2
    tail_blocks.reverse()

    omitted = len(blocks) - len(head_blocks) - len(tail_blocks)
    if omitted <= 0:
        return "\n\n".join(head_blocks + tail_blocks)

    return (
        "\n\n".join(head_blocks)
        + ELISION.format(n=omitted)
        + "\n\n".join(tail_blocks)
    )
