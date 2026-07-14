"""Small shared helpers for benchmark adapters (promoted from duplication).

Kept intentionally minimal — only logic that is genuinely used by 2+ adapters.
"""

from __future__ import annotations

import os
import sys
from itertools import zip_longest
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")


def interleave(groups: list[list[T]]) -> list[T]:
    """Round-robin flatten a list of lists, dropping exhausted slots.

    Used so a small ``--limit`` still spans every domain: given per-domain task
    lists ``[[a1,a2], [b1,b2,b3]]`` -> ``[a1, b1, a2, b2, b3]``.
    """
    out: list[T] = []
    for row in zip_longest(*groups):
        out.extend(item for item in row if item is not None)
    return out


def external_repo_on_path(
    default_path: Path,
    env_var: str,
    marker: str,
    clone_hint: str,
) -> Path:
    """Resolve an external benchmark repo, ensure it's importable, return it.

    Location comes from ``$<env_var>`` then ``default_path`` (never hardcoded at
    the call site beyond the default). Raises ``RuntimeError`` with ``clone_hint``
    if the repo (identified by the ``marker`` subdirectory) isn't present.
    """
    repo = Path(os.environ.get(env_var) or default_path)
    if not (repo / marker).is_dir():
        raise RuntimeError(clone_hint.format(repo=repo))
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    return repo
