"""ScriptedAgent — a deterministic policy that replays a fixed action list.

This is the offline / CI driver. It reads an ordered list of tool calls from
``task.metadata["oracle_actions"]`` and executes them against the environment,
so the full ``load -> setup -> run -> score`` pipeline (and the τ-bench /
AutomationBench state grading) can be demonstrated and tested **without any
model API key or network access**.

It is a legitimate ``Agent`` implementation (a scripted policy), not a mock —
the same code path the real agents use to touch the ``Env`` is exercised here.

``oracle_actions`` entry shape::

    {"tool": "cancel_order", "arguments": {"order_id": "O1001"}}
    {"tool": "message_user", "arguments": {"content": "Done — anything else?"}}

If a task has no ``oracle_actions``, the agent no-ops and returns an empty
trajectory (useful as a trivial baseline).
"""

from __future__ import annotations

import time

from harness.agent import Agent
from harness.benchmark import Env
from harness.registry import register_agent
from harness.schema import Step, Task, Trajectory


@register_agent("scripted")
class ScriptedAgent(Agent):
    """Replays ``task.metadata['oracle_actions']`` against the environment."""

    def __init__(self, max_steps: int = 50) -> None:
        self.max_steps = max_steps

    def run(self, task: Task, env: Env) -> Trajectory:
        start = time.perf_counter()
        traj = Trajectory()
        traj.add(Step(type="user_message", text=env.observation()))

        actions = task.metadata.get("oracle_actions", [])
        valid_tools = {t.name for t in env.tools()}
        last_output: str | None = None

        for action in actions[: self.max_steps]:
            name = action.get("tool")
            arguments = action.get("arguments", {}) or {}
            if name not in valid_tools:
                traj.add(Step(
                    type="error",
                    name=name,
                    arguments=arguments,
                    output=f"no such tool: {name!r}",
                    is_error=True,
                ))
                continue
            result = env.call_tool(name, arguments)
            traj.add(Step(
                type="tool_call",
                name=name,
                arguments=arguments,
                output=result.output,
                is_error=result.is_error,
            ))
            last_output = result.output

        traj.final_output = last_output
        traj.final_state = env.snapshot()
        traj.wall_time = time.perf_counter() - start
        return traj
