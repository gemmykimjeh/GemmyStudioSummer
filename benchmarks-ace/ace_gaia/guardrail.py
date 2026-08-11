"""Answer-leakage guardrail.

Hard requirement: the playbook must contain *strategies*, never memorised
answers. GAIA validation is reused across every iteration of this harness, so a
single leaked answer silently inflates all later comparisons — and would be
almost impossible to detect after the fact.

Two layers, in order of strength:

1. **Structural (primary).** The Reflector is never given ground truth — see
   ``roles.Reflector.reflect``, which accepts only a boolean outcome. What a
   model cannot see, it cannot distill. This module cannot be bypassed by
   prompt-injection or model misbehaviour because the data never arrives.

2. **Programmatic (this module, backstop).** Every candidate bullet is screened
   against the known answer set before it is committed. Catches the residual
   case where an answer reaches the trace summary (e.g. the agent echoes it) and
   is copied forward.

A prompt instruction alone is *not* enforcement and is not relied upon here.
"""

from __future__ import annotations

from .playbook import normalise


# Answers shorter than this are too generic to screen on ("2", "no", "red") —
# rejecting bullets containing them would produce constant false positives.
MIN_ANSWER_LEN = 4


class LeakageError(Exception):
    """Raised when a bullet would embed a ground-truth answer."""


def _tokens(text: str) -> list[str]:
    return normalise(text).split()


def leaks(text: str, answers: set[str]) -> str | None:
    """Return the offending answer if *text* embeds one, else None.

    Substring match on normalised text, plus an exact multi-token sequence check
    so "the answer is freshly squeezed lemon juice" is caught even if the
    surrounding wording differs.
    """
    hay = normalise(text)
    if not hay:
        return None
    hay_tokens = _tokens(text)

    for raw in answers:
        needle = normalise(raw)
        if len(needle) < MIN_ANSWER_LEN:
            continue

        # Plain substring — catches most cases.
        if needle in hay:
            return raw

        # Multi-token sequence match, robust to punctuation/spacing differences.
        n_tokens = needle.split()
        if len(n_tokens) > 1:
            for i in range(len(hay_tokens) - len(n_tokens) + 1):
                if hay_tokens[i:i + len(n_tokens)] == n_tokens:
                    return raw

    # A bare long number is answer-shaped, not strategy-shaped.
    for tok in hay_tokens:
        if tok.isdigit() and len(tok) >= 4 and tok in {normalise(a) for a in answers}:
            return tok
    return None


def screen(candidates: list[str], answers: set[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """Split candidate bullet texts into (clean, rejected).

    ``rejected`` carries (text, offending_answer) so rejections are auditable
    rather than silent — a silently dropped bullet looks identical to a
    Reflector that produced nothing.
    """
    clean: list[str] = []
    rejected: list[tuple[str, str]] = []
    for text in candidates:
        hit = leaks(text, answers)
        if hit is None:
            clean.append(text)
        else:
            rejected.append((text, hit))
    return clean, rejected


def audit(playbook, answers: set[str]) -> list[tuple[str, str]]:
    """Post-hoc sweep of an entire playbook. Returns [(bullet_id, answer), ...].

    Run this at the end of every pilot: it is the acceptance criterion
    "no bullet contains verbatim ground-truth answer text".
    """
    findings = []
    for b in playbook.bullets:
        hit = leaks(b.text, answers)
        if hit is not None:
            findings.append((b.id, hit))
    return findings