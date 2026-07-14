"""
auto_distill — non-LLM semantic-memory extraction (AEL idea).

AEL builds a "semantic memory" by aggregating episodic records into
cross-episode patterns. The per-episode extraction from a raw trajectory is a
deterministic, programmatic step (NO LLM call). We run it once per trajectory to
produce the semantic-memory input for the abstract Reflector call.

The fields are intentionally domain-adaptable: on benchmarks with explicit tool
calls they capture tool usage/order; on plain chain-of-thought traces (e.g.
finance) the tool fields degrade gracefully to empty and the outcome/pattern
fields still carry signal.

Cross-episode aggregation (AEL runs it periodically, ~every 10 episodes) is
out of scope here — a periodic pass can be added later (fits M7).
"""

import re
from collections import Counter
from typing import Any, Dict, List, Optional

# Loose markers for "an action/tool was invoked" across common trajectory styles:
#   Action: search[...]  |  <tool>foo</tool>  |  foo(...)  |  Step 3: ...  |  TOOL_CALL: bar
_ACTION_PATTERNS = [
    re.compile(r'Action\s*:\s*([A-Za-z_][\w.\-]*)', re.IGNORECASE),
    re.compile(r'<tool>\s*([A-Za-z_][\w.\-]*)', re.IGNORECASE),
    re.compile(r'tool_call\s*:?\s*["\']?([A-Za-z_][\w.\-]*)', re.IGNORECASE),
    re.compile(r'\b([a-z_][\w]*)\s*\(', ),  # bare function-call style
]

_STEP_PATTERN = re.compile(r'^\s*(?:step\s*\d+|thought\s*\d+|\d+[.)])\s*[:.\-]?\s*(.+)$',
                           re.IGNORECASE)

# Cues that an intermediate check/signal passed or failed.
_SIGNAL_CUES = re.compile(
    r'\b(match(?:es|ed)?|correct|verified?|confirm(?:ed)?|passed?|succeed(?:ed)?|'
    r'fail(?:ed|s)?|mismatch|incorrect|error|invalid|wrong)\b',
    re.IGNORECASE,
)

_ANSWER_PATTERN = re.compile(
    r'(?:final\s*answer|answer|result)\s*[:=]\s*(.+?)(?:\n|$)', re.IGNORECASE)


def _extract_tool_usage(trajectory: str) -> Dict[str, Any]:
    """Extract tool/action names, their counts, and first-seen order.

    Multiple patterns may match the SAME call site (e.g. ``Action: search`` and
    the bare-call ``search(`` both capture "search" at the same offset), so we
    dedupe by the capture-group start offset to avoid double counting.
    """
    _NOISE = {"if", "for", "while", "print", "return", "def", "int", "str",
              "float", "list", "dict", "len", "range", "format"}
    seen_at = {}  # offset -> name (first match at an offset wins)
    for pat in _ACTION_PATTERNS:
        for m in pat.finditer(trajectory):
            name = m.group(1)
            if name.lower() in _NOISE:
                continue
            seen_at.setdefault(m.start(1), name)
    # Order call sites by their position in the trajectory.
    ordered_names = [seen_at[off] for off in sorted(seen_at)]
    counts = Counter(ordered_names)
    order: List[str] = []
    for n in ordered_names:
        if n not in order:
            order.append(n)
    return {"counts": dict(counts), "order": order, "num_calls": len(ordered_names)}


def _extract_steps(trajectory: str) -> List[str]:
    """Pull out enumerated step/thought lines, trimmed."""
    steps = []
    for line in trajectory.splitlines():
        m = _STEP_PATTERN.match(line)
        if m:
            steps.append(m.group(1).strip())
    return steps


def _extract_recurring_patterns(steps: List[str], tool_counts: Dict[str, int]) -> Dict[str, Any]:
    """Find repeated motifs: repeated tools and repeated step-opening bigrams."""
    repeated_tools = {t: c for t, c in tool_counts.items() if c > 1}
    # Opening-word bigram over step lines as a cheap "state motif" signal.
    openings = [" ".join(s.lower().split()[:2]) for s in steps if s.split()]
    repeated_openings = {k: c for k, c in Counter(openings).items() if c > 1}
    return {
        "repeated_tools": repeated_tools,
        "repeated_step_motifs": repeated_openings,
    }


def _extract_signal_correctness(trajectory: str, is_correct: Optional[bool]) -> Dict[str, Any]:
    """Summarize intermediate check cues plus the final ground-truth signal."""
    cues = [m.group(0).lower() for m in _SIGNAL_CUES.finditer(trajectory)]
    return {
        "final_is_correct": is_correct,
        "intermediate_cue_counts": dict(Counter(cues)),
    }


def _extract_outcome_summary(trajectory: str, steps: List[str],
                             is_correct: Optional[bool]) -> Dict[str, Any]:
    """Capture the final answer span, outcome, and where the trace 'turned'."""
    answers = _ANSWER_PATTERN.findall(trajectory)
    final_answer = answers[-1].strip() if answers else None
    # "Turning point": last step mentioning a failure/mismatch cue, if any.
    turn_idx = None
    for i, s in enumerate(steps):
        if _SIGNAL_CUES.search(s):
            turn_idx = i
    return {
        "final_answer": final_answer,
        "num_steps": len(steps),
        "outcome": ("success" if is_correct else "failure") if is_correct is not None else "unknown",
        "turning_step_index": turn_idx,
    }


def auto_distill(trajectory: str, is_correct: Optional[bool] = None) -> Dict[str, Any]:
    """Extract a compact structured "semantic memory" from one raw trajectory.

    NO LLM CALL. Deterministic parse of the trajectory string.

    Args:
        trajectory: the generator's raw output / reasoning trace.
        is_correct: the benchmark signal for this episode, if known.

    Returns:
        A dict with fields: ``tool_usage``, ``signal_correctness``,
        ``recurring_patterns``, ``outcome_summary``. Empty/degraded fields are
        expected on plain-reasoning traces without tool calls.
    """
    trajectory = trajectory or ""
    tool_usage = _extract_tool_usage(trajectory)
    steps = _extract_steps(trajectory)
    return {
        "tool_usage": tool_usage,
        "signal_correctness": _extract_signal_correctness(trajectory, is_correct),
        "recurring_patterns": _extract_recurring_patterns(steps, tool_usage["counts"]),
        "outcome_summary": _extract_outcome_summary(trajectory, steps, is_correct),
    }


def format_semantic_memory(sem: Dict[str, Any]) -> str:
    """Render a SemanticMemory dict as compact text for prompt injection."""
    if not sem:
        return "(no semantic memory extracted)"
    tu = sem.get("tool_usage", {})
    sc = sem.get("signal_correctness", {})
    rp = sem.get("recurring_patterns", {})
    os_ = sem.get("outcome_summary", {})
    lines = [
        f"- outcome: {os_.get('outcome')} (steps={os_.get('num_steps')}, "
        f"turning_step={os_.get('turning_step_index')})",
        f"- final_answer: {os_.get('final_answer')}",
        f"- tools: order={tu.get('order')} counts={tu.get('counts')}",
        f"- recurring: tools={rp.get('repeated_tools')} "
        f"motifs={rp.get('repeated_step_motifs')}",
        f"- signal_cues: {sc.get('intermediate_cue_counts')} "
        f"final_correct={sc.get('final_is_correct')}",
    ]
    return "\n".join(lines)
