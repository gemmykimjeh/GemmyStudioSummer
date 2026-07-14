"""The two core abstractions: ``Env`` and ``Benchmark``.

``Env`` is the decoupling boundary. An agent never imports a benchmark and a
benchmark never imports an agent — they only ever meet through the generic
``Env`` tool interface. The agent sees a list of ``ToolSpec`` and a
``call_tool`` executor; whether those tools drive a VM, a Docker container, an
HTTP mock, or an in-memory simulator is entirely the benchmark's business.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from harness.schema import Result, Task, ToolResult, ToolSpec, Trajectory


class Env(ABC):
    """A prepared, per-task execution environment.

    Subclasses are created by ``Benchmark.setup`` and torn down by
    ``Benchmark.teardown``. They hold live state (a simulator DB, a container
    handle, an open socket) and are therefore *not* serializable — only the
    ``snapshot`` they expose for scoring is.
    """

    #: The task this env was prepared for.
    task: Task

    @abstractmethod
    def tools(self) -> list[ToolSpec]:
        """The tools available to the agent in this environment."""

    @abstractmethod
    def call_tool(self, name: str, arguments: dict) -> ToolResult:
        """Execute one tool call and return its result.

        Must never raise for a merely-invalid call — return a ``ToolResult``
        with ``is_error=True`` and an explanatory message so the agent can
        recover. Reserve exceptions for genuine environment failures.
        """

    def observation(self) -> str:
        """Initial context handed to the agent alongside the task prompt.

        Defaults to the task prompt; GUI/terminal envs may override with a
        rendered screen, a shell banner, etc.
        """
        return self.task.prompt

    def instructions(self) -> str:
        """Optional domain guidance for the agent's system prompt.

        E.g. a τ-bench domain policy ("never cancel a shipped order"). Kept
        generic: the agent appends this verbatim without knowing the benchmark.
        Defaults to empty.
        """
        return ""

    # -- optional conversational protocol ---------------------------------
    # Some environments are multi-turn dialogues with a (possibly simulated)
    # user: the agent's plain-text output is a *turn addressed to the user*,
    # not a signal that it is finished. Such envs set ``conversational = True``,
    # implement ``respond`` (deliver the agent's message, return the user's
    # reply), and report ``episode_done`` when the dialogue has ended. Agents
    # that support this protocol keep looping until ``episode_done`` instead of
    # stopping at the first plain-text turn. Non-conversational envs (the
    # default) are unaffected.
    conversational: bool = False

    def respond(self, text: str) -> str:
        """Deliver a natural-language turn to the user; return their reply."""
        raise NotImplementedError(
            "this Env is not conversational; set conversational=True and "
            "implement respond() to use the dialogue protocol"
        )

    def episode_done(self) -> bool:
        """True once a conversational episode has ended (user ended the chat)."""
        return False

    def snapshot(self) -> dict:
        """A JSON-serializable view of environment state, used for scoring.

        Defaults to empty — benchmarks that grade on final state override this.
        """
        return {}

    def workspace(self) -> str | None:
        """Optional **local directory** a native-tool agent can operate in directly.

        This is the hook for "bring-your-own-tools" (A-mode) agents — e.g. the
        Claude Agent SDK using its *own* Bash/Read/Write/Edit tools instead of the
        env's bridged ``tools()``. A benchmark whose task is editing files (e.g.
        SWE-bench: a repo checkout) returns a real path here; the native agent
        sets it as its working directory and the benchmark scores the resulting
        files. Container/VM-only envs (whose state isn't a local dir) return
        ``None`` — native-tool agents can't drive them and must use tool-bridge
        (B-)mode. Default ``None``.
        """
        return None

    def close(self) -> None:
        """Release resources. Called by ``Benchmark.teardown`` by default."""


class Benchmark(ABC):
    """A benchmark suite: how to load tasks, prepare envs, and score attempts.

    Every benchmark implements its own grading in ``score`` (final-state check,
    pass/fail, LLM-judge, answer match, ...) but always returns the common
    ``Result`` schema. There is deliberately **no** shared/universal grader.
    """

    #: Stable identifier used on the CLI and in the registry.
    name: str

    #: True for shape-faithful *mock* reproductions that are not wired to the
    #: real upstream suite. The runner stamps this onto every ``Result`` and the
    #: CLI/CSV surface a ``[MOCK]`` tag, so mock scores are never mistaken for
    #: official ones.
    mock: bool = False

    @abstractmethod
    def load_tasks(self, limit: int | None = None) -> list[Task]:
        """Return this benchmark's tasks, optionally capped at ``limit``."""

    @abstractmethod
    def setup(self, task: Task) -> Env:
        """Prepare the execution environment for one task."""

    @abstractmethod
    def score(self, task: Task, trajectory: Trajectory, env: Env) -> Result:
        """Grade one attempt into a ``Result`` using this benchmark's rules."""

    def teardown(self, env: Env) -> None:
        """Release the environment. Override for custom cleanup."""
        env.close()

    def report(self, results: list[Result]) -> str | None:
        """Optional benchmark-specific summary text, printed by the CLI.

        Lets a benchmark surface metrics the generic runner doesn't know about
        (e.g. AutomationBench's per-domain pass rate + false-success-claim rate,
        or a public-vs-official-score caveat). Defaults to nothing.
        """
        return None
