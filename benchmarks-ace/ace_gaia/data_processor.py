"""GAIA adapter for the ``data_processor`` interface ACE expects.

ACE calls exactly two methods on this object:

* ``answer_is_correct(predicted, target)`` — drives the reflection-round retry
  decision and the recorded score.
* ``evaluate_accuracy(answers, targets)`` — aggregate reporting.

Both delegate to GAIA's own ``question_scorer`` so that a score produced inside
the ACE loop is identical to one produced by ``benchmarks.gaia.get_score`` over
the same answers. Reimplementing the comparison here would let the two drift.
"""

from __future__ import annotations

from typing import Sequence

from benchmarks.gaia.scorer import question_scorer


class GAIADataProcessor:
    """Scoring surface for ACE, backed by GAIA's quasi-exact-match scorer."""

    def answer_is_correct(self, predicted: str, ground_truth: str) -> bool:
        """Whether *predicted* matches *ground_truth* under GAIA's rules."""
        if predicted is None or ground_truth is None:
            return False
        try:
            return bool(question_scorer(str(predicted), str(ground_truth)))
        except Exception:  # noqa: BLE001
            # question_scorer raises on some malformed numeric answers. A
            # scoring failure is a wrong answer, not a run failure.
            return False

    def evaluate_accuracy(
        self, answers: Sequence[str], targets: Sequence[str]
    ) -> float:
        """Fraction of *answers* matching *targets*; 0.0 for an empty set."""
        if not answers or not targets:
            return 0.0
        correct = sum(
            1 for a, t in zip(answers, targets) if self.answer_is_correct(a, t)
        )
        return correct / len(targets)
