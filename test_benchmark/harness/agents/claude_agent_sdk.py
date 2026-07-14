"""ClaudeAgentSDKAgent — the real **Claude Agent SDK** driven over the generic Env.

Unlike ``claude_sdk`` (which calls the raw Anthropic Messages API and runs *our*
hand-written tool loop), this agent hands control to Anthropic's **Claude Agent
SDK** (``claude-agent-sdk`` — the framework that powers Claude Code): its own
agentic loop, context management, and tool orchestration. We only bridge the two
worlds:

  * Each benchmark ``Env`` tool (``ToolSpec`` + ``call_tool``) is exposed to the
    SDK as an **in-process MCP tool** (``@tool`` + ``create_sdk_mcp_server``), so
    the SDK agent acts on the benchmark's sandbox, not the host.
  * A ``can_use_tool`` gate **allows only our ``mcp__env__*`` tools** and denies
    every built-in (Bash/Read/Write/WebSearch/...), so the SDK can't touch the
    host filesystem — the ``Env`` stays the sole boundary.
  * The SDK's message stream is collected back into our ``Trajectory``
    (assistant text, thinking, tool calls, tokens, and the SDK's own cost).

Two selectable modes (constructor ``native_tools`` / CLI ``--native-tools``):
  * **B-mode** (default): the SDK acts ONLY through the env's bridged
    ``mcp__env__*`` tools; the ``can_use_tool`` gate blocks host built-ins. A
    fixed, apples-to-apples toolset — good for comparing *reasoning* across
    agents. Works on every benchmark.
  * **A-mode** (``native_tools=True``): the SDK uses its OWN built-in tools
    (Bash/Read/Write/Edit/...) directly on the benchmark's local
    ``env.workspace()`` — the agent system evaluated *with its own tooling*
    (real-leaderboard style). Requires an **A-capable** benchmark (one that
    exposes ``workspace()``, e.g. ``swe_bench``); **B-only** benchmarks
    (container/VM/tool-API: terminal_bench, osworld, tau_bench, ...) raise a
    clear error telling you to drop ``--native-tools``.

This adapter is fully self-contained: it registers via ``@register_agent`` and
needs no changes to the core/runner/registry — exactly like ``scripted``,
``claude_sdk``, and ``hermes``. Requires the ``claude_agent_sdk`` extra
(``pip install -e ".[claude_agent_sdk]"``) and ``ANTHROPIC_API_KEY``.

Limitation: the SDK ``query`` is single-prompt, so the conversational
user-simulator protocol (``Env.respond``; e.g. real τ-bench) is not driven here —
this agent targets tool-based and tool-free envs. Documented in README.
"""

from __future__ import annotations

import asyncio
import os
import time

from harness.agent import Agent
from harness.benchmark import Env
from harness.registry import register_agent
from harness.schema import Step, Task, Trajectory

_SYSTEM_PROMPT = (
    "You are an autonomous agent completing a task using ONLY the provided "
    "environment tools (named mcp__env__*). Do not rely on host tools. Inspect "
    "and change the environment through those tools; when the task is fully "
    "complete, stop and give a short confirmation. If a tool errors, read it and "
    "adjust instead of repeating the same call."
)

# A-mode: the SDK uses its OWN built-in tools in the working directory.
_SYSTEM_PROMPT_NATIVE = (
    "You are an autonomous coding agent working in the current directory. Use "
    "your own tools (bash, file read/write/edit, search, ...) to complete the "
    "task by making the required changes to the files here. When the task is "
    "fully complete, stop and give a short confirmation."
)

_ENV_TOOL_PREFIX = "mcp__env__"


def _is_env_tool(tool_name: str) -> bool:
    """Only the benchmark Env's bridged tools may run (block host built-ins)."""
    return tool_name.startswith(_ENV_TOOL_PREFIX)


def _block_text(content) -> str:
    """Flatten a ToolResultBlock's content (str | list of blocks) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                parts.append(b.get("text") or b.get("content") or "")
            else:
                parts.append(getattr(b, "text", "") or "")
        return "\n".join(p for p in parts if p)
    return "" if content is None else str(content)


def _attach_tool_results(sdk, user_msg, traj) -> None:
    """Attach native ToolResultBlock outputs to the most recent tool_call step."""
    content = getattr(user_msg, "content", None)
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, sdk.ToolResultBlock):
            continue
        out = _block_text(getattr(block, "content", None))
        is_err = bool(getattr(block, "is_error", False))
        for step in reversed(traj.steps):
            if step.type == "tool_call" and step.output is None:
                step.output = out
                step.is_error = is_err
                break


def _usage_tokens(usage) -> int:
    """input+output tokens from a ResultMessage.usage (dict or object)."""
    if not usage:
        return 0
    def g(k):
        if isinstance(usage, dict):
            return usage.get(k) or 0
        return getattr(usage, k, 0) or 0
    return g("input_tokens") + g("output_tokens")


@register_agent("claude_agent_sdk")
class ClaudeAgentSDKAgent(Agent):
    """Drives the real Claude Agent SDK loop over the generic Env."""

    def __init__(
        self,
        model: str = "claude-opus-4-8",
        max_steps: int = 30,
        thinking: str | None = None,   # None | "adaptive"
        api_key: str | None = None,
        native_tools: bool = False,
    ) -> None:
        self.model = model
        self.max_steps = max_steps
        self.thinking = thinking
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        # native_tools=False (B-mode, default): the SDK acts ONLY through the env's
        #   bridged mcp__env__* tools (apples-to-apples fixed toolset; the gate
        #   blocks host built-ins).
        # native_tools=True  (A-mode): the SDK uses its OWN built-in tools
        #   (Bash/Read/Write/Edit/Grep/...) on env.workspace() — the agent system
        #   is evaluated *with its own tooling*, the real-leaderboard style. Needs
        #   an env that exposes a local workspace(); container/VM-only envs can't.
        self.native_tools = native_tools

    def _preflight(self):
        try:
            import claude_agent_sdk  # noqa: PLC0415 — lazy so core needs no SDK
        except ImportError as exc:  # pragma: no cover - import guard
            raise RuntimeError(
                "ClaudeAgentSDKAgent needs the Claude Agent SDK: "
                'pip install -e ".[claude_agent_sdk]"'
            ) from exc
        if not self._api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set; ClaudeAgentSDKAgent cannot run. "
                "Use the `scripted` agent for offline runs."
            )
        return claude_agent_sdk

    # -- Env tool -> in-process SDK MCP tool ------------------------------
    @staticmethod
    async def _invoke(env: Env, name: str, args, traj: Trajectory) -> dict:
        """Route one SDK tool call to the Env, record it, return MCP content."""
        call_args = dict(args or {})
        # env.call_tool is sync/blocking (docker exec, HTTP, ...) — run off-thread
        # so the SDK's event loop / subprocess I/O isn't blocked.
        res = await asyncio.to_thread(env.call_tool, name, call_args)
        traj.add(Step(type="tool_call", name=name, arguments=call_args,
                      output=res.output, is_error=res.is_error))
        return {"content": [{"type": "text", "text": res.output or ""}],
                "is_error": bool(res.is_error)}

    @classmethod
    def _make_tool(cls, sdk, env: Env, spec, traj: Trajectory):
        schema = spec.parameters or {"type": "object", "properties": {}}

        @sdk.tool(spec.name, spec.description, schema)
        async def _handler(args):  # SDK calls this with the model's tool input
            return await cls._invoke(env, spec.name, args, traj)

        return _handler

    def _build_options(self, sdk, env: Env, traj: Trajectory):
        """Dispatch to A-mode (native tools) or B-mode (env tool-bridge)."""
        if self.native_tools:
            return self._build_native_options(sdk, env)
        return self._build_env_options(sdk, env, traj)

    def _build_native_options(self, sdk, env: Env):
        """A-mode: the SDK uses its OWN built-in tools on the env's local workspace.

        Requires an A-capable benchmark (``env.workspace()`` returns a path).
        B-mode-only envs (container/VM/tool-API) return None -> clear error."""
        ws = env.workspace()
        if ws is None:
            raise RuntimeError(
                "native_tools (A-mode) needs a benchmark that exposes a local "
                f"workspace(), but {type(env).__name__} is B-mode ONLY — its "
                "environment is a container/VM/tool-API, not a local directory. "
                "A-capable today: swe_bench. For this benchmark, run without "
                "--native-tools to use the tool-bridge (B) mode.")
        system = _SYSTEM_PROMPT_NATIVE
        extra = env.instructions()
        if extra:
            system = f"{system}\n\n# Task-specific guidance\n{extra}"
        opts = sdk.ClaudeAgentOptions(
            model=self.model,
            system_prompt=system,
            cwd=ws,                       # its native tools operate here (= the sandbox)
            permission_mode="bypassPermissions",  # headless: run its own tools, no prompts
            max_turns=self.max_steps,
            setting_sources=[],
        )
        if self.thinking == "adaptive":
            opts.thinking = sdk.ThinkingConfigAdaptive()
        return opts

    def _build_env_options(self, sdk, env: Env, traj: Trajectory):
        """B-mode: restrict the SDK agent to the Env's bridged tools only."""
        sdk_tools = [self._make_tool(sdk, env, s, traj) for s in env.tools()]
        mcp_servers = {}
        if sdk_tools:
            mcp_servers["env"] = sdk.create_sdk_mcp_server(
                name="env", version="1.0.0", tools=sdk_tools)

        system = _SYSTEM_PROMPT
        extra = env.instructions()
        if extra:
            system = f"{system}\n\n# Task-specific guidance\n{extra}"

        async def _gate(tool_name, tool_input, context):
            if _is_env_tool(tool_name):
                return sdk.PermissionResultAllow()
            return sdk.PermissionResultDeny(
                message="host/built-in tools are disabled in this sandboxed "
                        "eval; use only the provided mcp__env__* tools")

        opts = sdk.ClaudeAgentOptions(
            model=self.model,
            system_prompt=system,
            mcp_servers=mcp_servers,
            # No allowed_tools allowlist on purpose: an allowlist auto-approves
            # matching tools BEFORE can_use_tool, shadowing the gate. Empty, EVERY
            # tool call falls through to _gate — the single authority that allows
            # mcp__env__* and denies all host built-ins.
            allowed_tools=[],
            can_use_tool=_gate,
            permission_mode="default",   # so the gate is consulted for host tools
            max_turns=self.max_steps,
            setting_sources=[],          # don't inherit the host's Claude Code config
        )
        if self.thinking == "adaptive":
            opts.thinking = sdk.ThinkingConfigAdaptive()
        return opts

    # -- run --------------------------------------------------------------
    def run(self, task: Task, env: Env) -> Trajectory:
        sdk = self._preflight()
        return asyncio.run(self._run_async(sdk, env))

    async def _run_async(self, sdk, env: Env) -> Trajectory:
        start = time.perf_counter()
        traj = Trajectory()
        traj.add(Step(type="user_message", text=env.observation()))
        opts = self._build_options(sdk, env, traj)

        # ClaudeSDKClient (streaming mode) — required for the can_use_tool gate;
        # it also cleanly handles in-process MCP tools + a single prompt.
        async with sdk.ClaudeSDKClient(options=opts) as client:
            await client.query(env.observation())
            # B-mode records tool calls in the tool handler; A-mode (native tools
            # have no handler) records them from the message stream instead.
            last_text, result_msg = await self._drive(
                sdk, client.receive_response(), traj,
                record_tool_uses=self.native_tools)

        self._finalize(traj, result_msg, last_text)
        traj.final_state = env.snapshot()
        traj.wall_time = time.perf_counter() - start
        return traj

    @staticmethod
    async def _drive(sdk, stream, traj: Trajectory, record_tool_uses: bool = False):
        """Consume the SDK message stream into ``traj``; return (last_text, result).

        ``record_tool_uses`` (A-mode): record native ToolUseBlocks as tool_call
        steps and attach ToolResultBlocks — in B-mode the tool handler already
        records them, so leave it False to avoid double-counting."""
        last_text = None
        result_msg = None
        async for msg in stream:
            if isinstance(msg, sdk.AssistantMessage):
                for block in msg.content:
                    if isinstance(block, sdk.TextBlock):
                        if block.text:
                            traj.add(Step(type="assistant_message", text=block.text))
                            last_text = block.text
                    elif isinstance(block, sdk.ThinkingBlock):
                        traj.add(Step(type="thinking", text=block.thinking))
                    elif record_tool_uses and isinstance(block, sdk.ToolUseBlock):
                        traj.add(Step(type="tool_call", name=block.name,
                                      arguments=dict(block.input or {})))
            elif record_tool_uses and isinstance(msg, sdk.UserMessage):
                _attach_tool_results(sdk, msg, traj)  # native tool outputs come back here
            elif isinstance(msg, sdk.ResultMessage):
                result_msg = msg
        return last_text, result_msg

    @staticmethod
    def _finalize(traj: Trajectory, result_msg, last_text: str | None) -> None:
        if result_msg is not None:
            traj.tokens = _usage_tokens(result_msg.usage)
            traj.cost = float(result_msg.total_cost_usd or 0.0)
            traj.final_output = (result_msg.result or last_text)
            if getattr(result_msg, "is_error", False) or result_msg.subtype != "success":
                traj.add(Step(type="error",
                              text=f"agent ended with subtype={result_msg.subtype}"))
        else:
            traj.final_output = last_text
