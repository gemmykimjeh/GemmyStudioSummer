"""The two ACE roles, implemented directly (arXiv 2510.04618).

    Generator -> the OpenHands agent (existing harness; not implemented here)
    Reflector -> distils a generalisable insight from a trace + binary outcome
    Curator   -> turns insights into incremental delta ops on the playbook

External dependencies: litellm, pydantic — both already used by the OpenHands
SDK. No dependency on ace-framework (Kayba).

Design principles
-----------------
1. **The Reflector never receives ground truth.** ``reflect()`` has no
   ground_truth parameter at all; only the boolean outcome crosses the
   boundary. This is enforced by structure rather than by prompt instruction,
   so no model behaviour can bypass it.

2. **Evidence citation is mandatory.** Every insight must quote a string that
   actually occurs in the trace, and the quote is verified mechanically. Credit
   assignment from a binary outcome over a long trace is fundamentally
   underdetermined, so a model will happily invent plausible-but-unfalsifiable
   lessons. Verifying the citation blocks that without relying on the model's
   good behaviour.

3. **The Curator does not ask the model what code can decide.** Similarity is
   arithmetic; the model is consulted only in the genuinely ambiguous band.

4. **The Curator never rewrites the playbook wholesale.** The paper attributes
   "context collapse" — the silent loss of accumulated detail — to monolithic
   rewrites, so every change here is a small, attributable delta.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .guardrail import screen
from .playbook import Playbook, normalise


# Curator similarity bands. Outside this range no model call is made.
BAND_LOW = 0.60      # below -> clearly a new strategy, auto-ADD
BAND_HIGH = 0.82     # above  -> clearly a duplicate, auto-merge

MAX_BULLETS = 25     # injection budget; evict lowest utility beyond this


# --------------------------------------------------------------------- LLM

def load_llm_config(path: str | Path) -> dict:
    """Read the same .llm_config JSON the harness uses."""
    return json.loads(Path(path).read_text())


class LLM:
    """Thin litellm wrapper. Deterministic by default (temperature 0)."""

    def __init__(self, model: str, api_key: str | None = None,
                 temperature: float = 0.0, max_tokens: int = 1500) -> None:
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens

    def json_call(self, system: str, user: str, retries: int = 1) -> Any:
        """Call the model and parse JSON. One retry on a parse failure."""
        import litellm

        last_err: Exception | None = None
        for _ in range(retries + 1):
            resp = litellm.completion(
                model=self.model,
                api_key=self.api_key,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            text = resp.choices[0].message.content or ""
            try:
                return _extract_json(text)
            except ValueError as e:            # noqa: PERF203
                last_err = e
                user += "\n\n(Your previous reply was not valid JSON. Reply with JSON only.)"
        raise last_err or ValueError("failed to parse JSON")


def _extract_json(text: str) -> Any:
    """Parse JSON from a model reply, tolerating code fences and prose."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = text.find(opener), text.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"could not parse JSON from model reply: {text[:200]!r}")


# --------------------------------------------------------------- Reflector

@dataclass
class Reflection:
    """Analysis of a single attempt."""

    evidence: str = ""            # verbatim quote from the trace
    what_went_wrong: str = ""     # localisation: where it diverged
    root_cause: str = ""          # why it diverged
    correct_approach: str = ""    # what should have happened
    insight: str | None = None    # the generalised strategy, or None
    evidence_verified: bool = False
    dropped_reason: str = ""
    raw: Any = None

    @property
    def insights(self) -> list[str]:
        """What gets handed to the Curator. Empty if verification failed."""
        if self.insight and self.evidence_verified:
            return [self.insight]
        return []


REFLECTOR_SYSTEM = """\
You analyse one attempt by an AI agent at a research task and extract a
reusable STRATEGY.

You are given: the task question, a summary of what the agent did, and whether
it succeeded. You are NOT given the correct answer. Do not try to guess it.

Work in this order, and report each step:
1. evidence  - quote the part of the trace your judgement rests on, copied
               VERBATIM. Do not summarise or paraphrase it. It must be a
               literal substring of the trace.
2. what_went_wrong - which step diverged and how (or, on success, what was
               decisive).
3. root_cause      - why that happened.
4. correct_approach - what should have been done at that point.
5. insight         - the above turned into ONE sentence that would help on a
               COMPLETELY UNRELATED task.

Rules for insight:
- Good: process. Tool selection, verification habits, output-format rules.
- Bad: task-specific facts, proper nouns, figures, anything answer-shaped.
- If there is nothing generalisable here, set insight to null.
  **null is a perfectly good answer and is far better than a vague sentence.**
- Imperative, one sentence, under 25 words.

For SUCCESSFUL attempts, set insight to null unless something notable happened
(recovery from an error, an unusual tool sequence, a format self-correction).
Platitudes distilled from ordinary successes only dilute the playbook.

Reply with JSON only:
{"evidence": "...", "what_went_wrong": "...", "root_cause": "...",
 "correct_approach": "...", "insight": "..." or null}
"""


class Reflector:
    """Extracts a generalisable insight from a single attempt."""

    def __init__(self, llm: LLM, min_evidence_len: int = 12) -> None:
        self.llm = llm
        self.min_evidence_len = min_evidence_len

    def reflect(self, *, question: str, trace_summary: str, success: bool,
                bullets_shown: str = "") -> Reflection:
        """Analyse one attempt.

        The absence of a ``ground_truth`` parameter is deliberate: the only
        outcome information crossing this boundary is a single boolean.
        """
        user = (
            f"TASK QUESTION:\n{question.strip()}\n\n"
            f"STRATEGIES SHOWN TO THE AGENT:\n{bullets_shown or '(none)'}\n\n"
            f"WHAT THE AGENT DID:\n{trace_summary.strip()}\n\n"
            f"OUTCOME: {'SUCCESS' if success else 'FAILURE'}"
        )
        data = self.llm.json_call(REFLECTOR_SYSTEM, user)
        if not isinstance(data, dict):
            return Reflection(raw=data, dropped_reason="reply was not a JSON object")

        insight = data.get("insight")
        insight = str(insight).strip() if insight else None
        r = Reflection(
            evidence=str(data.get("evidence") or "").strip(),
            what_went_wrong=str(data.get("what_went_wrong") or "").strip(),
            root_cause=str(data.get("root_cause") or "").strip(),
            correct_approach=str(data.get("correct_approach") or "").strip(),
            insight=insight,
            raw=data,
        )
        r.evidence_verified, r.dropped_reason = self._verify(r, trace_summary)
        return r

    def _verify(self, r: Reflection, trace_summary: str) -> tuple[bool, str]:
        """Check mechanically that the cited evidence occurs in the trace.

        This is the anti-hallucination core: a model can invent a plausible
        cause, but it cannot quote text that is not there.
        """
        if r.insight is None:
            return False, "no insight offered (normal)"
        if len(r.evidence) < self.min_evidence_len:
            return False, f"evidence too short ({len(r.evidence)} chars)"

        hay = normalise(trace_summary)
        needle = normalise(r.evidence)
        if needle in hay:
            return True, ""

        # Relaxed comparison to absorb whitespace/punctuation differences.
        best = 0.0
        n_tokens = needle.split()
        window = len(n_tokens)
        h_tokens = hay.split()
        for i in range(max(1, len(h_tokens) - window + 1)):
            seg = " ".join(h_tokens[i:i + window])
            best = max(best, SequenceMatcher(None, needle, seg).ratio())
            if best >= 0.85:
                return True, ""
        return False, f"evidence not found in trace (best match {best:.2f})"


# ----------------------------------------------------------------- Curator

@dataclass
class DeltaOp:
    op: str                       # ADD | UPDATE | MERGE | SKIP | REJECTED_LEAK | EVICT
    text: str | None = None
    bullet_id: str | None = None
    detail: str = ""


CURATOR_SYSTEM = """\
You maintain a playbook of strategies for an AI agent.

You are given one new insight and the single existing entry closest to it in
meaning. Decide one of:

  UPDATE - same strategy, but the new insight is sharper or more general.
           Supply improved text to replace the existing entry.
  ADD    - they overlap superficially but are genuinely distinct strategies.
  SKIP   - the new insight adds nothing over the existing entry.

Rules:
- One strategy per entry. Never merge several ideas into one.
- Imperative, under 25 words.
- No task-specific facts, figures or proper nouns.

Reply with JSON only:
{"op": "UPDATE"|"ADD"|"SKIP", "text": "...", "reason": "..."}
"""


class Curator:
    """Applies insights to the playbook as incremental delta operations."""

    def __init__(self, llm: LLM, max_bullets: int = MAX_BULLETS) -> None:
        self.llm = llm
        self.max_bullets = max_bullets

    def curate(self, playbook: Playbook, insights: list[str], *,
               answers: set[str] | None = None,
               task_id: str | None = None) -> list[DeltaOp]:
        """Screen for leaks -> route by similarity -> apply -> enforce budget."""
        applied: list[DeltaOp] = []
        if not insights:
            return applied

        clean, rejected = screen(insights, answers or set())
        applied += [DeltaOp(op="REJECTED_LEAK", text=t, detail=f"matched answer {a!r}")
                    for t, a in rejected]

        for text in clean:
            applied.append(self._route(playbook, text, answers or set(), task_id))

        applied += self._enforce_budget(playbook)
        return applied

    def _route(self, playbook: Playbook, text: str,
               answers: set[str], task_id: str | None) -> DeltaOp:
        """Handle automatically by similarity; consult the model only when ambiguous."""
        best, score = self._closest(playbook, text)

        # Clearly a duplicate -> auto-merge, no model call.
        if best is not None and score > BAND_HIGH:
            bullet, _ = playbook.add(text, task_id=task_id)
            return DeltaOp(op="MERGE", text=bullet.text, bullet_id=bullet.id,
                           detail=f"similarity {score:.2f} > {BAND_HIGH}")

        # Clearly new -> auto-add, no model call.
        if best is None or score < BAND_LOW:
            bullet, _ = playbook.add(text, task_id=task_id)
            return DeltaOp(op="ADD", text=bullet.text, bullet_id=bullet.id,
                           detail=f"similarity {score:.2f} < {BAND_LOW}")

        # Ambiguous band only.
        user = (f"EXISTING ENTRY [{best.id}]: {best.text}\n\n"
                f"NEW INSIGHT: {text}\n\n"
                f"measured similarity: {score:.2f}")
        try:
            data = self.llm.json_call(CURATOR_SYSTEM, user)
        except Exception as e:                                  # noqa: BLE001
            bullet, _ = playbook.add(text, task_id=task_id)
            return DeltaOp(op="ADD", text=bullet.text, bullet_id=bullet.id,
                           detail=f"model call failed ({e.__class__.__name__}); added conservatively")

        op = str(data.get("op", "")).upper() if isinstance(data, dict) else ""
        new_text = (data.get("text") or text).strip() if isinstance(data, dict) else text

        # The Curator rewrote the text, so screen it again.
        _, bad = screen([new_text], answers)
        if bad:
            return DeltaOp(op="REJECTED_LEAK", text=new_text,
                           detail=f"matched answer {bad[0][1]!r} after rewrite")

        if op == "UPDATE" and playbook.update(best.id, new_text):
            return DeltaOp(op="UPDATE", text=new_text, bullet_id=best.id,
                           detail=f"similarity {score:.2f} (ambiguous band)")
        if op == "SKIP":
            return DeltaOp(op="SKIP", text=text, bullet_id=best.id,
                           detail=f"similarity {score:.2f}; existing entry sufficient")
        bullet, _ = playbook.add(new_text, task_id=task_id)
        return DeltaOp(op="ADD", text=bullet.text, bullet_id=bullet.id,
                       detail=f"similarity {score:.2f} (ambiguous band, judged distinct)")

    @staticmethod
    def _closest(playbook: Playbook, text: str):
        """The most similar existing entry and its similarity."""
        cand = normalise(text)
        best, best_score = None, 0.0
        for b in playbook.bullets:
            s = SequenceMatcher(None, cand, normalise(b.text)).ratio()
            if s > best_score:
                best, best_score = b, s
        return best, best_score

    def _enforce_budget(self, playbook: Playbook) -> list[DeltaOp]:
        """Evict the least useful entries once over budget.

        Without a cap the playbook only grows: injection cost rises every task
        and the signal dilutes.
        """
        ops: list[DeltaOp] = []
        while len(playbook) > self.max_bullets:
            victim = min(playbook.bullets, key=lambda b: (b.score, b.updated_at))
            playbook.remove(victim.id)
            ops.append(DeltaOp(op="EVICT", text=victim.text, bullet_id=victim.id,
                               detail=f"over budget {self.max_bullets}, score {victim.score}"))
        return ops
