"""In-process HTTP MCP server that exposes a benchmark ``Env`` as MCP tools.

This is the tool-bridge for external agents (Hermes) that run in a *separate
process* but can act as MCP clients. We serve the current task's ``Env`` over
Streamable HTTP on a loopback port; the agent's tool calls are routed back to
``env.call_tool()`` / ``env.respond()`` — mutating the same in-process ``Env``
the benchmark will score afterward.

Only the loopback interface is bound, on an ephemeral port, for the lifetime of
one task.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from contextlib import asynccontextmanager

import mcp.types as mtypes
import uvicorn
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Mount

from harness.benchmark import Env


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class EnvMCPBridge:
    """Serve one ``Env`` as an MCP server over loopback Streamable HTTP."""

    def __init__(self, env: Env) -> None:
        self.env = env
        self.include_respond = getattr(env, "conversational", False)
        self.port = _free_port()
        self.calls: list[tuple[str, dict]] = []  # tool calls received, for diag
        self._lock = threading.Lock()  # env calls are serialized
        self._thread: threading.Thread | None = None
        self._uvicorn: uvicorn.Server | None = None  # set in start()

    @property
    def url(self) -> str:
        # Streamable HTTP endpoint (trailing slash matters for some clients).
        return f"http://127.0.0.1:{self.port}/mcp"

    def _tool_list(self) -> list[mtypes.Tool]:
        tools = [
            mtypes.Tool(name=s.name, description=s.description,
                        inputSchema=s.parameters)
            for s in self.env.tools()
        ]
        if self.include_respond:
            tools.append(mtypes.Tool(
                name="respond",
                description="Send a message to the user and receive their reply.",
                inputSchema={"type": "object",
                             "properties": {"content": {"type": "string"}},
                             "required": ["content"]},
            ))
        return tools

    def _call(self, name: str, arguments: dict) -> str:
        with self._lock:
            self.calls.append((name, dict(arguments or {})))
            if name == "respond" and self.include_respond:
                return str(self.env.respond((arguments or {}).get("content", "")))
            result = self.env.call_tool(name, arguments or {})
            return str(result.output)

    def _build_app(self) -> Starlette:
        server: Server = Server("harness-env-bridge")

        @server.list_tools()
        async def _list_tools() -> list[mtypes.Tool]:  # noqa: ANN202
            return self._tool_list()

        @server.call_tool()
        async def _call_tool(name: str, arguments: dict):  # noqa: ANN202
            out = await asyncio.to_thread(self._call, name, arguments)
            return [mtypes.TextContent(type="text", text=out)]

        manager = StreamableHTTPSessionManager(
            app=server, event_store=None, json_response=True, stateless=True)

        async def handle(scope, receive, send):
            await manager.handle_request(scope, receive, send)

        @asynccontextmanager
        async def lifespan(app):  # noqa: ANN202
            async with manager.run():
                yield

        return Starlette(routes=[Mount("/mcp", app=handle)], lifespan=lifespan)

    def start(self, timeout: float = 10.0) -> str:
        """Start the server in a daemon thread; return the MCP URL when ready."""
        app = self._build_app()
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port,
                                log_level="warning", lifespan="on")
        self._uvicorn = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._uvicorn.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if getattr(self._uvicorn, "started", False):
                return self.url
            time.sleep(0.05)
        raise RuntimeError("MCP bridge server failed to start in time")

    def stop(self) -> None:
        if self._uvicorn is not None:
            self._uvicorn.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5.0)
