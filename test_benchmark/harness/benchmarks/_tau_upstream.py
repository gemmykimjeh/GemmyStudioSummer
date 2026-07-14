"""Locate and import the upstream sierra-research/tau-bench package.

The upstream repo is expected at ``C:/GemmyStudioSummer/test_benchmark/external/tau-bench`` (clone
it there), or at ``$TAU_BENCH_PATH``. We add it to ``sys.path`` rather than
pip-installing it, so the heavy transitive deps (google-generativeai, mistralai)
are avoided — only ``litellm`` (the user simulator backend) is required, via the
``tau_bench`` extra: ``pip install -e ".[tau_bench]"``.
"""

from __future__ import annotations

import functools
from pathlib import Path

from harness.benchmarks._util import external_repo_on_path

_DEFAULT_REPO = Path(__file__).resolve().parents[2] / "external" / "tau-bench"
_CLONE_HINT = (
    "upstream tau-bench not found at {repo}. Clone it:\n"
    "  git clone https://github.com/sierra-research/tau-bench.git {repo}\n"
    "(or set TAU_BENCH_PATH). Then: pip install -e \".[tau_bench]\"."
)


@functools.lru_cache(maxsize=1)
def load_upstream():
    """Import upstream tau-bench, returning ``(get_env, Action, UserStrategy)``.

    Raises ``RuntimeError`` with actionable guidance if the repo isn't cloned or
    ``litellm`` isn't installed — we never silently fall back to the mock here;
    the caller decides fallback via the ``real`` flag.
    """
    external_repo_on_path(_DEFAULT_REPO, "TAU_BENCH_PATH", "tau_bench", _CLONE_HINT)
    try:
        from tau_bench.envs import get_env
        from tau_bench.envs.user import UserStrategy
        from tau_bench.types import Action
    except ImportError as exc:  # pragma: no cover - depends on env
        raise RuntimeError(
            "tau-bench found but its deps are missing "
            f"({exc}). Install the extra: pip install -e \".[tau_bench]\" "
            "(pulls litellm)."
        ) from exc
    return get_env, Action, UserStrategy


def upstream_available() -> bool:
    try:
        load_upstream()
        return True
    except RuntimeError:
        return False
