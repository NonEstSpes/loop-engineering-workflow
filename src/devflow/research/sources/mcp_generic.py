"""Generic MCP-based research source."""

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
    return dict(config)


class McpGenericSource(ResearchSource):
    """Driver that calls a configurable tool on any MCP server."""

    name = "mcp_generic"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._client = McpClient(_server_config(config))
        self._tool = config["tool"]
        self._arguments = config.get("arguments", {})
        self._query_field = config.get("query_field", "query")

    def search(self, request: ResearchRequest) -> list[ResearchFinding]:
        """Call the configured tool, injecting the request query."""
        arguments: dict[str, Any] = dict(self._arguments)
        arguments[self._query_field] = request.query
        result = self._client.call_tool(self._tool, arguments=arguments)
        text = _extract_text(result)
        return [
            ResearchFinding(
                source=self.name,
                query=request.query,
                title=f"{self._tool} result",
                content=text,
                metadata={"tool": self._tool, "query": request.query},
            ),
        ]

    def close(self) -> None:
        self._client.close()
