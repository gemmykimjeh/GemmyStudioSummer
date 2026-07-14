"""Tests for the ClaudeAgentSDKAgent (real Claude Agent SDK, bridged over Env).

All offline — the SDK package is imported for its dataclasses/decorators, but no
API call is made (``query`` is monkeypatched). A live run spends API and needs
ANTHROPIC_API_KEY, so it is exercised manually.
"""

from __future__ import annotations

import asyncio

import pytest

from harness import registry
from harness.agents import claude_agent_sdk as ca
from harness.benchmark import Env
from harness.schema import Task, ToolResult, ToolSpec, Trajectory

sdk = pytest.importorskip("claude_agent_sdk")


class FakeEnv(Env):
    def __init__(self, tools, workspace=None):
        self._tools = tools
        self._workspace = workspace
        self.calls = []

    def tools(self):
        return self._tools

    def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments or {})))
        return ToolResult(output=f"ran {name} with {arguments}", is_error=False)

    def observation(self):
        return "complete the task"

    def instructions(self):
        return "domain policy here"

    def workspace(self):
        return self._workspace  # None = B-only; a path = A-capable

    def snapshot(self):
        return {"ok": True}


_TOOL = ToolSpec(name="bash", description="run a shell command",
                 parameters={"type": "object",
                             "properties": {"command": {"type": "string"}},
                             "required": ["command"]})


def test_registered_as_real_agent():
    registry.load_builtins()
    a = registry.get_agent("claude_agent_sdk", api_key="test")
    assert isinstance(a, ca.ClaudeAgentSDKAgent)


def test_is_env_tool_gate_logic():
    assert ca._is_env_tool("mcp__env__bash") is True
    assert ca._is_env_tool("Bash") is False
    assert ca._is_env_tool("mcp__other__x") is False


def test_usage_tokens_dict_and_object():
    assert ca._usage_tokens({"input_tokens": 10, "output_tokens": 5}) == 15
    class U:
        input_tokens = 7
        output_tokens = 3
    assert ca._usage_tokens(U()) == 10
    assert ca._usage_tokens(None) == 0


def test_invoke_routes_to_env_and_records_step():
    env = FakeEnv([_TOOL])
    traj = Trajectory()
    out = asyncio.run(ca.ClaudeAgentSDKAgent._invoke(env, "bash", {"command": "ls"}, traj))
    assert env.calls == [("bash", {"command": "ls"})]
    assert out["content"][0]["text"].startswith("ran bash")
    assert out["is_error"] is False
    tc = [s for s in traj.steps if s.type == "tool_call"]
    assert len(tc) == 1 and tc[0].name == "bash" and tc[0].arguments == {"command": "ls"}


def test_build_options_restricts_to_env_tools():
    agent = ca.ClaudeAgentSDKAgent(api_key="test")
    # tool env -> one mcp server "env", allowlist mcp__env__*
    opts = agent._build_options(sdk, FakeEnv([_TOOL]), Trajectory())
    assert "env" in opts.mcp_servers
    # restriction is enforced by the can_use_tool gate (no allowlist to shadow it)
    assert opts.allowed_tools == []
    assert opts.can_use_tool is not None
    assert "domain policy here" in opts.system_prompt   # env.instructions() folded in
    # tool-free env -> no servers
    opts0 = agent._build_options(sdk, FakeEnv([]), Trajectory())
    assert opts0.mcp_servers == {}


def test_permission_gate_allows_only_env_tools():
    agent = ca.ClaudeAgentSDKAgent(api_key="test")
    opts = agent._build_options(sdk, FakeEnv([_TOOL]), Trajectory())
    gate = opts.can_use_tool
    allow = asyncio.run(gate("mcp__env__bash", {}, None))
    deny = asyncio.run(gate("Bash", {}, None))
    assert isinstance(allow, sdk.PermissionResultAllow)
    assert isinstance(deny, sdk.PermissionResultDeny)


def test_native_mode_options_use_workspace_cwd(tmp_path):
    # A-mode: SDK uses its OWN tools in env.workspace(); no MCP env server.
    agent = ca.ClaudeAgentSDKAgent(api_key="test", native_tools=True)
    env = FakeEnv([], workspace=str(tmp_path))
    opts = agent._build_options(sdk, env, Trajectory())
    assert str(opts.cwd) == str(tmp_path)
    assert opts.permission_mode == "bypassPermissions"
    assert not opts.mcp_servers            # native tools, not the env bridge


def test_native_mode_errors_on_b_only_benchmark():
    # B-only env (workspace() is None) + A-mode -> clear, actionable error.
    agent = ca.ClaudeAgentSDKAgent(api_key="test", native_tools=True)
    with pytest.raises(RuntimeError, match="B-mode ONLY"):
        agent._build_options(sdk, FakeEnv([_TOOL], workspace=None), Trajectory())


def test_drive_records_native_tool_uses():
    # A-mode recording: ToolUseBlock -> tool_call step, ToolResultBlock -> output.
    async def stream():
        yield sdk.AssistantMessage(
            content=[sdk.ToolUseBlock(id="1", name="Bash", input={"command": "ls"})],
            model="m")
        yield sdk.UserMessage(
            content=[sdk.ToolResultBlock(tool_use_id="1", content="file.txt")])
        yield sdk.ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="s", result="done")

    traj = Trajectory()
    asyncio.run(ca.ClaudeAgentSDKAgent._drive(sdk, stream(), traj, record_tool_uses=True))
    tc = [s for s in traj.steps if s.type == "tool_call"]
    assert len(tc) == 1 and tc[0].name == "Bash" and tc[0].arguments == {"command": "ls"}
    assert tc[0].output == "file.txt"


def test_block_text_flattens():
    assert ca._block_text("hi") == "hi"
    assert ca._block_text([{"type": "text", "text": "a"}, {"text": "b"}]) == "a\nb"
    assert ca._block_text(None) == ""


def test_preflight_requires_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    agent = ca.ClaudeAgentSDKAgent(api_key=None)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        agent.run(Task(id="t", benchmark="x", prompt="p"), FakeEnv([]))


def test_drive_and_finalize_collect_trajectory():
    # Feed a realistic SDK message stream (assistant text + success ResultMessage)
    # through the collection helpers — no client, no API.
    async def stream():
        yield sdk.AssistantMessage(content=[sdk.TextBlock(text="all done")], model="m")
        yield sdk.ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="s", total_cost_usd=0.012,
            usage={"input_tokens": 100, "output_tokens": 20}, result="all done")

    traj = Trajectory()
    last_text, result_msg = asyncio.run(
        ca.ClaudeAgentSDKAgent._drive(sdk, stream(), traj))
    ca.ClaudeAgentSDKAgent._finalize(traj, result_msg, last_text)
    assert traj.final_output == "all done"
    assert traj.tokens == 120
    assert traj.cost == pytest.approx(0.012)
    assert any(s.type == "assistant_message" and s.text == "all done" for s in traj.steps)
