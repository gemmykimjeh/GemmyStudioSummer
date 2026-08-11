"""ACE on the OpenHands GAIA harness.

The ACE implementation itself is vendored unmodified under ``vendor/`` (see
``vendor/README.md``). This package supplies only the parts needed to run it
against OpenHands:

* ``runner.ACEGAIAEvaluation`` — ACE's per-sample loop, driven by the harness's
  ``Evaluation`` loop.
* ``trace.compact_trace`` — the event log reduced to ACE's ``reasoning_trace``.
* ``data_processor.GAIADataProcessor`` — GAIA scoring behind ACE's interface.
* ``run_ace`` — CLI entry point.

Note: ``playbook.py``, ``roles.py`` and ``guardrail.py`` are an earlier
from-scratch reimplementation of ACE. They are superseded by ``vendor/`` and are
no longer imported by anything here.
"""

from .data_processor import GAIADataProcessor
from .runner import ACE_DEFAULTS, ACEGAIAEvaluation
from .trace import compact_trace


__all__ = [
    "ACEGAIAEvaluation",
    "ACE_DEFAULTS",
    "GAIADataProcessor",
    "compact_trace",
]
