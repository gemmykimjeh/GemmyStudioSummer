"""Locate and import the upstream zapier/AutomationBench package.

Expected at ``C:/GemmyStudioSummer/test_benchmark/external/AutomationBench`` (clone it there) or
``$AUTOMATION_BENCH_PATH``. Added to ``sys.path`` rather than pip-installed, so
we avoid the heavy ``verifiers`` runtime — the upstream ``__init__`` already
makes that import optional, and the pieces we use (domains, WorldState, the
assertion rubric, the api toolset) don't need it. Only ``datasets`` is required
(the ``automation_bench`` extra).

Windows note: some upstream files are read with the platform default encoding;
on a cp949 locale that raises ``UnicodeDecodeError``. Run the harness in Python
UTF-8 mode — the CLI enables this automatically via a re-exec guard; for direct
API use set ``PYTHONUTF8=1`` / launch ``python -X utf8``.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

from harness.benchmarks._util import external_repo_on_path

_DEFAULT_REPO = Path(__file__).resolve().parents[2] / "external" / "AutomationBench"
_CLONE_HINT = (
    "upstream AutomationBench not found at {repo}. Clone it:\n"
    "  git clone https://github.com/zapier/AutomationBench.git {repo}\n"
    "(or set AUTOMATION_BENCH_PATH). Then: pip install -e \".[automation_bench]\"."
)


@functools.lru_cache(maxsize=1)
def load_upstream():
    """Import upstream AutomationBench; return a namespace of the pieces we use.

    Raises ``RuntimeError`` with guidance if the repo isn't cloned or
    ``datasets`` is missing. Non-strict assertions are enabled so a single buggy
    assertion counts as a failed check instead of crashing a whole run.
    """
    external_repo_on_path(
        _DEFAULT_REPO, "AUTOMATION_BENCH_PATH", "automationbench", _CLONE_HINT)
    # Errors in a single assertion -> that assertion fails, run continues.
    os.environ.setdefault("AUTOMATIONBENCH_STRICT_ASSERTIONS", "0")
    try:
        from automationbench.domains import (
            DOMAINS,
            PUBLIC_DOMAINS,
            get_domain_dataset,
        )
        from automationbench.rubric import partial_credit, task_completed_correctly
        from automationbench.schema.world import WorldState
        from automationbench.tools.api.encode import base64_encode
        from automationbench.tools.api.fetch import api_fetch
        from automationbench.tools.api.search import api_search
    except ImportError as exc:  # pragma: no cover - depends on env
        raise RuntimeError(
            "AutomationBench found but its deps are missing "
            f"({exc}). Install the extra: pip install -e \".[automation_bench]\" "
            "(pulls datasets)."
        ) from exc

    # Service-gating helper (replicated from upstream runner.py, which imports
    # verifiers at module top and so can't be imported directly).
    service_fields = sorted(
        (str(f) for f in WorldState.model_fields if f != "meta"),
        key=len, reverse=True,
    )

    def service_for_name(name: str) -> str | None:
        for field in service_fields:
            if name == field or name.startswith(field + "_"):
                return field
        return None

    def compute_allowed_services(initial_state, assertions, zapier_tools):
        allowed: set[str] = set()
        for key in initial_state:
            if key != "meta" and key in WorldState.model_fields:
                allowed.add(key)
        for a in assertions or []:
            s = service_for_name(str(a.get("type", "")))
            if s:
                allowed.add(s)
        for t in zapier_tools or []:
            s = service_for_name(t)
            if s:
                allowed.add(s)
        return sorted(allowed)

    return {
        "get_domain_dataset": get_domain_dataset,
        "PUBLIC_DOMAINS": list(PUBLIC_DOMAINS),
        "DOMAINS": DOMAINS,
        "WorldState": WorldState,
        "partial_credit": partial_credit,
        "task_completed_correctly": task_completed_correctly,
        "api_search": api_search,
        "api_fetch": api_fetch,
        "base64_encode": base64_encode,
        "compute_allowed_services": compute_allowed_services,
    }


def strip_none_values(obj):
    """Drop None values HuggingFace Datasets inject for schema normalization."""
    if isinstance(obj, dict):
        return {k: strip_none_values(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [strip_none_values(v) for v in obj if v is not None]
    return obj


def upstream_available() -> bool:
    try:
        load_upstream()
        return True
    except RuntimeError:
        return False
