"""Generic ReAct transcript -> Trajectory conversion, shared across agents.

Any agent that produces a chat/tool-calling transcript — OpenAI-style
``{role, content, tool_calls}`` or ShareGPT-style ``{from, value}`` — turns it
into the harness :class:`Trajectory` with a single call here. Keeping this
conversion agent-agnostic means a new external-agent adapter (Hermes today, some
other CLI/subprocess agent tomorrow) reuses it instead of reimplementing step
extraction — so adding agents never bottlenecks on trajectory plumbing.

The same reusable pieces for an external agent are: the :class:`~harness.agent.Agent`
interface, :class:`~harness.agents._mcp_bridge.EnvMCPBridge` (expose the env's
tools over MCP), and this converter.
"""

from __future__ import annotations

import json

from harness.schema import Step, Trajectory


def messages_to_trajectory(messages, trajectory: Trajectory | None = None) -> Trajectory:
    """Append ReAct steps parsed from ``messages`` to (or into) a Trajectory.

    Thought → ``assistant_message``, action → ``tool_call`` (with its
    observation attached as the step's ``output``), user turns → ``user_message``.
    Unknown/foreign shapes are skipped rather than raising.
    """
    traj = trajectory if trajectory is not None else Trajectory()
    for m in messages:
        if not isinstance(m, dict):
            continue
        if "role" in m:
            _handle_openai(m, traj)
        elif "from" in m:
            _handle_sharegpt(m, traj)
    return traj


def _handle_openai(m: dict, traj: Trajectory) -> None:
    role = m.get("role")
    content = m.get("content")
    text = content if isinstance(content, str) else None
    if role == "assistant":
        if text:
            traj.add(Step(type="assistant_message", text=text))
        for tc in m.get("tool_calls") or []:
            fn = (tc or {}).get("function", {})
            traj.add(Step(type="tool_call", name=fn.get("name"),
                          arguments=_as_dict(fn.get("arguments"))))
    elif role in ("tool", "tool_response"):
        _attach_observation(traj, text)
    elif role == "user":
        if text:
            traj.add(Step(type="user_message", text=text))


def _handle_sharegpt(m: dict, traj: Trajectory) -> None:
    who, value = m.get("from"), m.get("value")
    val = value if isinstance(value, str) else None
    if who in ("gpt", "assistant"):
        traj.add(Step(type="assistant_message", text=val))
    elif who in ("tool", "observation"):
        _attach_observation(traj, value)
    elif who in ("human", "user"):
        traj.add(Step(type="user_message", text=val))


def _as_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {"_": parsed}
        except json.JSONDecodeError:
            return {"_raw": value}
    return {}


def _attach_observation(traj: Trajectory, text) -> None:
    """Attach a tool result to the most recent tool_call lacking output."""
    obs = text if isinstance(text, str) else json.dumps(text, default=str)
    for step in reversed(traj.steps):
        if step.type == "tool_call" and step.output is None:
            step.output = obs
            return
    traj.add(Step(type="assistant_message", text=obs))
