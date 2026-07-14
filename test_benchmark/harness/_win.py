"""Windows shim shared by the CLIs."""

from __future__ import annotations

import os
import sys


def ensure_utf8_mode(module: str) -> None:
    """On Windows, re-exec once in Python UTF-8 mode.

    Some benchmarks read UTF-8 data files with the platform default encoding;
    under a cp949/legacy locale that raises UnicodeDecodeError. UTF-8 mode can
    only be set at interpreter startup, so we re-exec ``-X utf8`` the first time.
    Guarded by a sentinel env var. ``module`` is the ``-m`` target to re-run
    (e.g. ``"harness.run"`` / ``"harness.experiment"``).
    """
    if (sys.platform == "win32" and not sys.flags.utf8_mode
            and os.environ.get("HARNESS_UTF8_REEXEC") != "1"):
        os.environ["HARNESS_UTF8_REEXEC"] = "1"
        os.environ["PYTHONUTF8"] = "1"
        os.execv(sys.executable,
                 [sys.executable, "-X", "utf8", "-m", module, *sys.argv[1:]])
