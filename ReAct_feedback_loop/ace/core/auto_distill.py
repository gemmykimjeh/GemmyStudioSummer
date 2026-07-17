"""
auto_distill — GDPval-aware, non-LLM episodic record builder for the ABSTRACT
(cross-task) side of the loop.

Original AEL idea: build "semantic memory" by AGGREGATING episodic records across
episodes. We distill EACH task's attempt into a compact structured record (NO LLM),
buffer them, and every k tasks the abstract Reflector reads the buffered records to
propose CROSS-TASK principles (per-task abstraction from a single sample is weak).

The extraction understands OUR trajectory format — the Generator's JSON
`{reasoning, bullet_ids, final_answer}` — and the rubric grader's feedback string
(`Rubric score X ...` + `Criteria NOT met: - (n pts) <criterion>`), NOT ReAct
`Action:/Step N:` traces. So it actually captures signal on GDPval.
"""

import json
import re
from collections import Counter
from typing import Any, Dict, List, Optional

# Deliverable-type hint from the TASK prompt (what artifact is asked for).
_DELIVERABLE_PATTERNS = [
    (re.compile(r'\b(spreadsheet|excel|xlsx|workbook|pivot|\.csv)\b', re.I), "spreadsheet"),
    (re.compile(r'\b(pptx|powerpoint|slide deck|slides?|presentation)\b', re.I), "slides"),
    (re.compile(r'\b(\.docx|word document|memo|letter|contract|agreement|will|policy|brief)\b', re.I), "document"),
    (re.compile(r'\b(\.pdf|report)\b', re.I), "pdf/report"),
    (re.compile(r'\b(e-?mail)\b', re.I), "email"),
    (re.compile(r'\b(code|script|program|SQL|query|function)\b', re.I), "code"),
    (re.compile(r'\b(video|commercial|broadcast spot|storyboard)\b', re.I), "media"),
]
_SCORE_RE = re.compile(r'Rubric score\s*([0-9.]+)', re.I)
_MISS_LINE_RE = re.compile(r'^\s*-\s*\([^)]*\)\s*(?:\[REQUIRED\]\s*)?(.+?)\s*$')


def _deliverable_type(question: str) -> str:
    for pat, name in _DELIVERABLE_PATTERNS:
        if pat.search(question or ""):
            return name
    return "text"


def _parse_trajectory(trajectory: str) -> Dict[str, Any]:
    """Our Generator emits JSON `{reasoning, bullet_ids, final_answer}`."""
    try:
        d = json.loads(trajectory)
        if isinstance(d, dict):
            fa = d.get("final_answer") or ""
            if not isinstance(fa, str):
                fa = json.dumps(fa, ensure_ascii=False)
            return {"final_answer": fa.strip()[:240],
                    "n_bullets_cited": len(d.get("bullet_ids") or [])}
    except (ValueError, TypeError):
        pass
    return {"final_answer": (trajectory or "").strip()[:240], "n_bullets_cited": 0}


def _parse_feedback(feedback: str):
    """From the grader feedback string: rubric score + list of missed criteria."""
    score = None
    m = _SCORE_RE.search(feedback or "")
    if m:
        try:
            score = float(m.group(1))
        except ValueError:
            pass
    missed: List[str] = []
    capture = False
    for line in (feedback or "").splitlines():
        if "Criteria NOT met" in line:
            capture = True
            continue
        if capture:
            mm = _MISS_LINE_RE.match(line)
            if mm:
                missed.append(mm.group(1).strip())
    return score, missed


def auto_distill(trajectory: str, question: str = "", feedback: str = "",
                 is_correct: Optional[bool] = None) -> Dict[str, Any]:
    """Distill ONE GDPval task attempt into a compact episodic record (NO LLM).

    Args:
        trajectory: the Generator's raw JSON output.
        question:   the task prompt (for the deliverable-type hint).
        feedback:   the grader feedback string (rubric score + missed criteria).
        is_correct: pass/fail signal.

    Returns a small dict — the abstract Reflector reads a window of these.
    """
    traj = _parse_trajectory(trajectory or "")
    score, missed = _parse_feedback(feedback or "")
    return {
        "deliverable_type": _deliverable_type(question),
        "passed": bool(is_correct) if is_correct is not None else None,
        "score": score,
        "missed_criteria": missed[:8],
        "final_answer_excerpt": traj["final_answer"],
        "n_bullets_cited": traj["n_bullets_cited"],
    }


def format_window(records: List[Dict[str, Any]]) -> str:
    """Render a WINDOW of episodic records for the abstract Reflector prompt,
    surfacing cross-episode structure (types, outcomes, and — most useful — which
    rubric criteria were repeatedly missed)."""
    if not records:
        return "(no episodes)"
    lines = []
    for i, r in enumerate(records, 1):
        lines.append(
            f"Episode {i}: type={r.get('deliverable_type')} "
            f"passed={r.get('passed')} score={r.get('score')}")
        miss = r.get("missed_criteria") or []
        if miss:
            lines.append("  missed: " + " | ".join(miss[:6]))
    # Cross-episode roll-up: which criteria / types recur (the real signal).
    all_miss = Counter(m.lower() for r in records for m in (r.get("missed_criteria") or []))
    repeated = {m: c for m, c in all_miss.items() if c > 1}
    types = Counter(r.get("deliverable_type") for r in records)
    lines.append("")
    lines.append(f"ACROSS the window: deliverable_types={dict(types)}")
    if repeated:
        top = sorted(repeated.items(), key=lambda kv: -kv[1])[:6]
        lines.append("REPEATED missed criteria (appear in >1 episode): "
                     + " ; ".join(f'{m} (x{c})' for m, c in top))
    return "\n".join(lines)


# Back-compat: some older imports referenced this name.
def format_semantic_memory(record: Any) -> str:
    if isinstance(record, list):
        return format_window(record)
    return format_window([record] if record else [])
