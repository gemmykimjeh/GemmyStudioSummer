"""Provenance capture for reproducible run directories.

A run directory records *exactly* what produced it: our git commit (+ dirty
flag), the Hermes-project commit (read-only sibling repo at ``C:/hermes``), the
Python/platform, and the versions of the key packages. This is what makes a run
re-runnable and a report auditable.

Everything degrades gracefully: not-a-git-repo, no Hermes checkout, or a missing
package all become ``None`` with an explanatory note rather than an error.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

_TRACKED_PACKAGES = ("anthropic", "datasets", "litellm", "mcp", "pydantic",
                     "swebench")


def _git(cwd: str | Path, *args: str) -> str | None:
    try:
        p = subprocess.run(["git", "-C", str(cwd), *args],
                           capture_output=True, text=True, timeout=10)
    except Exception:  # noqa: BLE001 - git missing / path gone
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def _pkg(name: str) -> str | None:
    try:
        return _pkg_version(name)
    except PackageNotFoundError:
        return None
    except Exception:  # noqa: BLE001
        return None


def collect_provenance(*, root: str | Path = ".",
                       hermes_path: str | None = None,
                       extra: dict | None = None) -> dict:
    """Snapshot the reproducibility-relevant state of the environment."""
    root = Path(root)
    commit = _git(root, "rev-parse", "HEAD")
    git_dirty = None
    if commit is not None:
        git_dirty = bool(_git(root, "status", "--porcelain"))

    hp = hermes_path or os.environ.get("HERMES_PATH") or "C:/hermes"
    hermes_commit = _git(hp, "rev-parse", "HEAD")

    prov = {
        "git_commit": commit,
        "git_dirty": git_dirty,
        "git_note": None if commit else "not a git repository",
        "hermes_path": str(hp),
        "hermes_commit": hermes_commit,
        "hermes_note": None if hermes_commit else "no Hermes git checkout found",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": {name: _pkg(name) for name in _TRACKED_PACKAGES},
    }
    if extra:
        prov.update(extra)
    return prov
