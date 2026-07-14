"""Shared base for Phase-2 stub benchmarks.

Each stub implements the full ``Benchmark`` interface so the runner, registry,
and CLI already work with it — only ``load_tasks``/``setup``/``score`` raise
``NotImplementedError`` with a message pointing at the file's TODO/run notes.
Filling a stub in later is purely additive; no core code changes.
"""

from __future__ import annotations

from harness.benchmark import Benchmark, Env
from harness.schema import Result, Task, Trajectory


class StubBenchmark(Benchmark):
    """A benchmark whose adapter is wired but whose execution is a stub.

    Subclasses set ``name`` (via ``@register_benchmark``) and ``run_notes`` (a
    short summary of how to actually run the upstream benchmark). Every method
    raises with that guidance so the failure is self-explaining.
    """

    #: Human-readable summary of how to run the real benchmark. Set per subclass.
    run_notes: str = "See the module docstring."

    def _todo(self, method: str) -> NotImplementedError:
        return NotImplementedError(
            f"{type(self).__name__}.{method} is a Phase-2 stub.\n{self.run_notes}"
        )

    def load_tasks(self, limit: int | None = None) -> list[Task]:
        raise self._todo("load_tasks")

    def setup(self, task: Task) -> Env:
        raise self._todo("setup")

    def score(self, task: Task, trajectory: Trajectory, env: Env) -> Result:
        raise self._todo("score")
