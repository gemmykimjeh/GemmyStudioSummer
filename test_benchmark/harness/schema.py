"""Common data schemas shared across every agent and benchmark.

These pydantic models are the *only* types that cross the agent/benchmark
boundary. A benchmark emits ``Task`` objects; an agent consumes a ``Task`` (plus
a generic :class:`~harness.benchmark.Env`) and returns a ``Trajectory``; the
benchmark scores that ``Trajectory`` into a ``Result``. Because scoring is
delegated per-benchmark but always funnels into one ``Result`` schema, wildly
different grading schemes (pass/fail, partial credit, LLM-judge, Elo) all
compare on equal footing.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

StepType = Literal[
    "assistant_message",  # free-text the agent produced
    "tool_call",          # the agent invoked an env tool
    "user_message",       # a (possibly simulated) user turn
    "thinking",           # model reasoning, if surfaced
    "error",              # something went wrong during a step
]


class Step(BaseModel):
    """A single event in an agent's trajectory.

    ``tool_call`` steps carry ``name``/``arguments``/``output``; message steps
    carry ``text``. Keeping every event in one model lets scorers walk a
    trajectory uniformly.
    """

    type: StepType
    text: str | None = None
    name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    output: str | None = None
    is_error: bool = False


class Task(BaseModel):
    """A single unit of work handed to an agent.

    ``prompt`` is the natural-language instruction. ``metadata`` is a free-form
    bag the benchmark controls — it may hold grading goals, a user-simulator
    script, or oracle actions (used by ``ScriptedAgent`` for offline demos).
    Agents should treat ``metadata`` as opaque unless documented otherwise.
    """

    id: str
    benchmark: str
    prompt: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Trajectory(BaseModel):
    """The record of what an agent did while attempting a task."""

    steps: list[Step] = Field(default_factory=list)
    final_output: str | None = None
    final_state: dict[str, Any] = Field(default_factory=dict)
    tokens: int = 0
    cost: float = 0.0
    wall_time: float = 0.0

    def add(self, step: Step) -> None:
        self.steps.append(step)

    def tool_calls(self) -> list[Step]:
        return [s for s in self.steps if s.type == "tool_call"]


class Result(BaseModel):
    """The unified outcome of scoring one task attempt.

    Every benchmark returns this regardless of its native grading scheme:
    ``success`` is the binary verdict, ``score`` is a normalized [0, 1] number
    (or a benchmark-defined float), and ``metrics`` holds any extra per-benchmark
    numbers (partial-credit breakdowns, judge sub-scores, Elo, etc.).
    """

    task_id: str
    benchmark: str
    agent: str
    success: bool
    #: 0-based trial index when a task is run multiple times (pass^k). 0 for
    #: single-shot runs.
    trial: int = 0
    score: float = 0.0
    metrics: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    cost: float = 0.0
    wall_time: float = 0.0
    #: True when produced by a shape-faithful mock benchmark (not an official
    #: upstream score). Stamped by the runner from ``Benchmark.mock``.
    mock: bool = False


class ToolSpec(BaseModel):
    """A tool the environment exposes to the agent.

    ``parameters`` is a JSON Schema object. Agents translate these specs into
    whatever their underlying model expects (Anthropic tool definitions, an
    OpenAI-style function list, a ReAct action grammar, ...).
    """

    name: str
    description: str
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )


class ToolResult(BaseModel):
    """The result of executing one tool call against the environment."""

    output: str
    is_error: bool = False
