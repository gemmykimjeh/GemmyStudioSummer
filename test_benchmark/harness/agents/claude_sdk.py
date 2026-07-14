"""ClaudeSDKAgent — a real Anthropic function-calling agent loop.

Runs the standard manual tool-use loop against the Anthropic Messages API:
translate the env's ``ToolSpec`` list into Anthropic tool definitions, let the
model call them, execute each call against the ``Env``, feed results back, and
repeat until the model stops calling tools or the step budget is exhausted.

Requires the ``anthropic`` extra (``pip install -e ".[claude]"``) and an
``ANTHROPIC_API_KEY`` in the environment. The import is lazy so the rest of the
harness (and the offline ``scripted`` agent) works without the SDK installed.

Model: defaults to ``claude-opus-4-8`` (Anthropic's most capable Opus-tier
model). Thinking is left off so the tool loop stays simple and replay-free;
pass ``thinking="adaptive"`` to enable adaptive thinking.
"""

from __future__ import annotations

import os
import time

from harness.agent import Agent
from harness.benchmark import Env
from harness.registry import register_agent
from harness.schema import Step, Task, Trajectory

# Per-million-token prices (USD): (input, output). Cache read ~0.1x input,
# cache write ~1.25x input. Keep in sync with current Anthropic pricing.
_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5": (10.0, 50.0),
}

_SYSTEM_PROMPT = (
    "You are an autonomous agent completing a task by calling the available "
    "tools. Use tools to inspect and change the environment; do not ask the "
    "user to do things you can do yourself. When the task is fully complete, "
    "stop calling tools and reply with a short confirmation of what you did. "
    "If a tool returns an error, read it and adjust rather than repeating the "
    "same call."
)


def _price_for(model: str) -> tuple[float, float]:
    for prefix, price in _PRICING.items():
        if model.startswith(prefix):
            return price
    return (0.0, 0.0)


@register_agent("claude_sdk")
class ClaudeSDKAgent(Agent):
    """Anthropic Messages API function-calling loop over the generic Env."""

    def __init__(
        self,
        model: str = "claude-opus-4-8",
        max_steps: int = 30,
        max_tokens: int = 4096,
        thinking: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.max_steps = max_steps
        self.max_tokens = max_tokens
        self.thinking = thinking  # None | "adaptive"
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = None  # lazily constructed

    def _client_or_raise(self):
        if self._client is not None:
            return self._client
        try:
            import anthropic  # noqa: PLC0415 — lazy so core needs no SDK
        except ImportError as exc:  # pragma: no cover - import guard
            raise RuntimeError(
                "ClaudeSDKAgent needs the anthropic SDK: "
                'pip install -e ".[claude]"'
            ) from exc
        if not self._api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set; ClaudeSDKAgent cannot run. "
                "Use the `scripted` agent for offline runs."
            )
        self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def run(self, task: Task, env: Env) -> Trajectory:
        client = self._client_or_raise()
        start = time.perf_counter()
        traj = Trajectory()

        tools = [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.parameters,
            }
            for spec in env.tools()
        ]

        system = _SYSTEM_PROMPT
        extra = env.instructions()
        if extra:
            system = f"{system}\n\n# Task-specific guidance\n{extra}"

        traj.add(Step(type="user_message", text=env.observation()))
        messages = [{"role": "user", "content": env.observation()}]

        in_price, out_price = _price_for(self.model)
        last_text: str | None = None

        for _ in range(self.max_steps):
            kwargs = dict(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                tools=tools,
                messages=messages,
            )
            if self.thinking == "adaptive":
                kwargs["thinking"] = {"type": "adaptive"}
            response = client.messages.create(**kwargs)

            # Telemetry.
            usage = response.usage
            traj.tokens += (usage.input_tokens or 0) + (usage.output_tokens or 0)
            traj.cost += (
                (usage.input_tokens or 0) * in_price
                + (usage.output_tokens or 0) * out_price
            ) / 1_000_000

            # Record assistant text/thinking; collect tool-use blocks.
            turn_text_parts: list[str] = []
            tool_uses = []
            for block in response.content:
                if block.type == "text":
                    turn_text_parts.append(block.text)
                    traj.add(Step(type="assistant_message", text=block.text))
                elif block.type == "thinking":
                    traj.add(Step(type="thinking", text=block.thinking))
                elif block.type == "tool_use":
                    tool_uses.append(block)
            turn_text = "\n".join(turn_text_parts) if turn_text_parts else None
            if turn_text is not None:
                last_text = turn_text

            conversational = getattr(env, "conversational", False)

            if response.stop_reason != "tool_use":
                # No tool call this turn. In a conversational env, plain text is
                # a turn addressed to the user — deliver it and keep going until
                # the user ends the dialogue. Otherwise, the agent is finished.
                if conversational and turn_text and not env.episode_done():
                    reply = env.respond(turn_text)
                    traj.add(Step(type="user_message", text=reply))
                    messages.append({"role": "assistant", "content": response.content})
                    messages.append({"role": "user", "content": reply})
                    if env.episode_done():
                        break
                    continue
                break

            # Keep the full assistant turn (tool_use blocks included) in history.
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for tu in tool_uses:
                result = env.call_tool(tu.name, tu.input or {})
                traj.add(Step(
                    type="tool_call",
                    name=tu.name,
                    arguments=dict(tu.input or {}),
                    output=result.output,
                    is_error=result.is_error,
                ))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": result.output,
                    "is_error": result.is_error,
                })
            messages.append({"role": "user", "content": tool_results})

            # A terminate tool (e.g. transfer_to_human_agents) can end the chat.
            if conversational and env.episode_done():
                break

        traj.final_output = last_text
        traj.final_state = env.snapshot()
        traj.wall_time = time.perf_counter() - start
        return traj
