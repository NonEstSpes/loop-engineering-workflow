"""Research source that queries a Graphify MCP server."""

from __future__ import annotations

from typing import Any

from mcp.types import CallToolResult, EmbeddedResource, TextContent

from devflow.research.mcp_client import McpClient
from devflow.research.schemas import ResearchFinding, ResearchRequest
from devflow.research.sources.base import ResearchSource


def _extract_text(result: CallToolResult) -> str:
    """Flatten MCP tool result content into a single text string."""
    parts: list[str] = []
    for item in result.content:
        if isinstance(item, TextContent):
            parts.append(item.text)
        elif isinstance(item, EmbeddedResource):
            resource = item.resource
            if hasattr(resource, "text"):
                parts.append(str(resource.text))
            elif hasattr(resource, "blob"):
                parts.append("<binary resource>")
    return "\n".join(parts)


def _server_config(config: dict[str, Any]) -> dict[str, Any]:
    """Resolve server connection settings from common config keys."""
    for key in ("server", "connection", "params"):
        if key in config:
            return config[key]
    server_config = dict(config)
    # Allow `server_url` as an alias for SSE `url`.
    if "server_url" in server_config and "url" not in server_config:
        server_config["url"] = server_config.pop("server_url")
    return server_config


class GraphifyMcpSource(ResearchSource):
    """Driver for graphify-style symbol/file search via MCP."""

    name = "graphify_mcp"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._client = McpClient(_server_config(config))
        self._symbol_tool = config.get("symbol_tool", "search_symbols")
        self._file_tool = config.get("file_tool", "search_files")
        self._max_results = int(config.get("max_results", 10))

    def _call_search(self, tool_name: str, query: str, limit: int) -> list[ResearchFinding]:
        if not tool_name:
            return []
        result = self._client.call_tool(
            tool_name,
            arguments={
                "query": query,
                "limit": limit,
            },
        )
        text = _extract_text(result)
        if not text.strip():
            return []
        return [
            ResearchFinding(
                source=self.name,
                query=query,
                title=f"{tool_name} result",
                content=text,
                metadata={"tool": tool_name, "query": query},
            ),
        ]

    def search(self, request: ResearchRequest) -> list[ResearchFinding]:
        """Search symbols and files for the request query."""
        findings: list[ResearchFinding] = []
        query = request.query
        max_results = request.max_results or self._max_results

        try:
            findings.extend(self._call_search(self._symbol_tool, query, max_results))
        except Exception as exc:  # pragma: no cover - defensive
            findings.append(
                ResearchFinding(
                    source=self.name,
                    query=query,
                    title=f"{self._symbol_tool} error",
                    content=f"{self._symbol_tool} failed: {exc}",
                    confidence=0.0,
                    metadata={"tool": self._symbol_tool, "error": str(exc)},
                )
            )

        try:
            findings.extend(self._call_search(self._file_tool, query, max_results))
        except Exception as exc:  # pragma: no cover - defensive
            findings.append(
                ResearchFinding(
                    source=self.name,
                    query=query,
                    title=f"{self._file_tool} error",
                    content=f"{self._file_tool} failed: {exc}",
                    confidence=0.0,
                    metadata={"tool": self._file_tool, "error": str(exc)},
                )
            )

        return findings

    def close(self) -> None:
        self._client.close()
