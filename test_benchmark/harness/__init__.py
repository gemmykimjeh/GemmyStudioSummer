"""agent-bench-harness: plug any agent into any of 7 benchmark suites.

Public surface: the two abstractions (:class:`Agent`, :class:`Benchmark`,
:class:`Env`), the shared schema (:class:`Task`, :class:`Trajectory`,
:class:`Result`, :class:`ToolSpec`, :class:`ToolResult`), the :mod:`registry`,
and the :func:`~harness.runner.run` orchestrator.
"""

from __future__ import annotations

from harness.agent import Agent
from harness.benchmark import Benchmark, Env
from harness.schema import (
    Result,
    Step,
    Task,
    ToolResult,
    ToolSpec,
    Trajectory,
)

__all__ = [
    "Agent",
    "Benchmark",
    "Env",
    "Task",
    "Trajectory",
    "Result",
    "Step",
    "ToolSpec",
    "ToolResult",
]

__version__ = "0.1.0"
