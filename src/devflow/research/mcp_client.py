"""Synchronous facade over the official MCP Python SDK."""

from __future__ import annotations

import asyncio
import os
import threading
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters, Tool
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult

# Network/transport environment variables forwarded into every stdio MCP
# subprocess. The MCP SDK's get_default_environment() only whitelists a handful
# of OS vars (PATH, SYSTEMROOT, ...), so proxy and CA-bundle settings would be
# silently dropped unless we re-inject them here. SSE servers run in-process and
# already inherit the full os.environ, so they need no special handling.
_FORWARDED_NETWORK_ENV_VARS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "no_proxy",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
)


def _forward_network_env(env: dict[str, str] | None) -> dict[str, str]:
    """Return ``env`` with proxy/CA-bundle variables copied from ``os.environ``.

    Values already present in ``env`` win over ``os.environ`` (setdefault), so a
    caller can force a specific proxy/cert for one server if needed. Variables
    that are unset in both places are simply absent from the result.
    """
    merged: dict[str, str] = dict(env or {})
    for var in _FORWARDED_NETWORK_ENV_VARS:
        value = os.getenv(var)
        if value:
            merged.setdefault(var, value)
    return merged


class McpClient:
    """Sync MCP client that connects to a server over stdio or SSE.

    The SDK's transport/session code is async, so this wrapper runs an event
    loop in a background thread and exposes synchronous ``list_tools`` and
    ``call_tool`` methods.
    """

    def __init__(self, server_config: dict[str, Any]) -> None:
        self.server_config = server_config
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None
        self._validate_config()

    def _validate_config(self) -> None:
        transport = self.server_config.get("transport", "stdio")
        if transport not in {"stdio", "sse"}:
            raise ValueError(
                f"Unsupported MCP transport '{transport}'. Use 'stdio' or 'sse'."
            )
        if transport == "stdio" and "command" not in self.server_config:
            raise ValueError("stdio MCP server config requires 'command'.")
        if transport == "sse" and "url" not in self.server_config:
            raise ValueError("sse MCP server config requires 'url'.")

    def _start_loop(self) -> None:
        if self._loop is not None:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    def _stop_loop(self) -> None:
        loop, self._loop = self._loop, None
        thread, self._thread = self._thread, None
        if loop is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=5)

    def connect(self) -> None:
        """Open the MCP session in a background event loop."""
        if self._session is not None:
            return
        self._start_loop()
        assert self._loop is not None
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._connect_async(), self._loop
            )
            future.result(timeout=30)
        except Exception:
            self.close()
            raise

    async def _connect_async(self) -> None:
        self._exit_stack = AsyncExitStack()
        transport = self.server_config.get("transport", "stdio")
        try:
            if transport == "stdio":
                params = StdioServerParameters(
                    command=self.server_config["command"],
                    args=self.server_config.get("args", []),
                    env=_forward_network_env(self.server_config.get("env")),
                )
                read, write = await self._exit_stack.enter_async_context(
                    stdio_client(params)
                )
            else:
                read, write = await self._exit_stack.enter_async_context(
                    sse_client(url=self.server_config["url"])
                )
            self._session = await self._exit_stack.enter_async_context(
                ClientSession(read, write)
            )
            await self._session.initialize()
        except Exception:
            await self._exit_stack.aclose()
            self._exit_stack = None
            raise

    def list_tools(self) -> list[Tool]:
        """Return the tools exposed by the connected MCP server."""
        self.connect()
        assert self._session is not None and self._loop is not None
        future = asyncio.run_coroutine_threadsafe(
            self._session.list_tools(), self._loop
        )
        result = future.result(timeout=60)
        return list(result.tools)

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> CallToolResult:
        """Call ``name`` on the MCP server with the supplied arguments."""
        self.connect()
        assert self._session is not None and self._loop is not None
        future = asyncio.run_coroutine_threadsafe(
            self._session.call_tool(name, arguments=arguments or {}), self._loop
        )
        return future.result(timeout=60)

    def close(self) -> None:
        """Close the MCP session and stop the background loop."""
        exit_stack, self._exit_stack = self._exit_stack, None
        if exit_stack is not None and self._loop is not None:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    exit_stack.aclose(), self._loop
                )
                future.result(timeout=10)
            except Exception:
                pass
        self._session = None
        self._stop_loop()

    def __enter__(self) -> McpClient:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
