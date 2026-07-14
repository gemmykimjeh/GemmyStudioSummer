"""The ``Agent`` abstraction — the plug-in point for any harness.

Any agent that implements ``run(task, env) -> Trajectory`` can be evaluated on
every benchmark. The agent is handed the generic ``Env`` so it can discover and
call tools without knowing which benchmark produced them.

.. note::
   The original design sketch wrote ``run(self, task)``. In practice the agent
   must be able to *act* on the environment (call tools, message a simulated
   user), so ``run`` also receives the ``Env`` — the generic tool boundary that
   keeps agents and benchmarks mutually ignorant. See README > Architecture.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from harness.benchmark import Env
from harness.schema import Task, Trajectory


class Agent(ABC):
    """A pluggable agent/harness under evaluation."""

    #: Stable identifier used on the CLI and in the registry.
    name: str

    @abstractmethod
    def run(self, task: Task, env: Env) -> Trajectory:
        """Attempt ``task`` inside ``env`` and return the action trajectory.

        The returned ``Trajectory`` records every step (tool calls, messages),
        the final output, and cost/token/wall-time telemetry. The benchmark
        then scores the trajectory and/or the resulting ``env.snapshot()``.
        """
