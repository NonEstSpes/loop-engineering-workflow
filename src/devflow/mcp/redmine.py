"""Redmine task source adapter.

Wraps the external ``runekaagaard/mcp-redmine`` MCP server (launched over
stdio) and exposes it through the :class:`TaskSource` interface. All network
work is delegated to :class:`devflow.research.mcp_client.McpClient`; this
adapter is a thin mapping layer between Redmine's REST resources and the
:class:`devflow.state.Task` model.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import yaml
from mcp.types import CallToolResult, EmbeddedResource, TextContent

from devflow.mcp.base import TaskSource
from devflow.research.mcp_client import McpClient
from devflow.state import Task

logger = logging.getLogger(__name__)

# Maps the reporter's verdict-derived statuses (see nodes/reporter.py) to
# canonical Redmine status names. Names are resolved to ids at runtime via
# /issue_statuses.json; if resolution fails the name is passed through.
_VERDICT_STATUS_NAMES: dict[str, str] = {
    "resolved": "Resolved",
    "pending": "Feedback",
    "rejected": "Rejected",
    "escalated": "Feedback",
}

# Adapter status values that are valid as Redmine ``status_id`` *filter*
# values for GET /issues.json and must be passed through untouched.
_STATUS_FILTERS = {"open", "closed", "new"}


def _extract_text(result: CallToolResult) -> str:
    """Flatten an MCP tool result into a single text string (YAML/JSON).

    ``runekaagaard/mcp-redmine`` wraps every response body in an
    ``<insecure-content-HASH>...</insecure-content-HASH>`` pair so the host
    LLM treats it as untrusted content. Those markers are not valid YAML/JSON,
    so strip them before handing the text to the parser.
    """
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
    return _strip_insecure_content_markers("\n".join(parts))


# Matches ``<insecure-content-HASH>`` and the matching closing tag, where HASH
# is a short hex suffix (e.g. ``8d06470fc6c94f88``). Greedy on the body so a
# single regex removes both tags of a pair regardless of newlines.
_INSECURE_CONTENT_RE = re.compile(
    r"</?insecure-content-[0-9a-fA-F]+>",
)


def _strip_insecure_content_markers(text: str) -> str:
    """Remove ``<insecure-content-HASH>`` open/close markers from ``text``."""
    return _INSECURE_CONTENT_RE.sub("", text).strip()


def _unwrap_envelope(parsed: Any) -> Any:
    """Return the Redmine payload, unwrapping the MCP server's envelope.

    ``runekaagaard/mcp-redmine`` wraps every response as
    ``{status_code, body, error}``. We surface transport-level failures and
    hand back the inner ``body`` (Redmine's actual JSON payload) to callers.
    """
    if not isinstance(parsed, dict):
        return parsed
    if not {"status_code", "body"} <= parsed.keys():
        return parsed
    status = parsed.get("status_code")
    body = parsed.get("body")
    error = parsed.get("error")
    # Treat only 2xx as success; anything else is a transport/API failure.
    if isinstance(status, int) and not (200 <= status < 300):
        raise RuntimeError(
            f"Redmine request failed with status {status}"
            + (f": {error}" if error else "")
        )
    return body if body is not None else {}


class RedmineTaskSource(TaskSource):
    """Fetch tasks from Redmine via an external Redmine MCP server.

    The adapter talks to ``runekaagaard/mcp-redmine`` (or any compatible server
    exposing a ``redmine_request`` tool) over stdio. Connection is lazy: the
    subprocess is only started on the first tool call.
    """

    name = "redmine"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.url = (config.get("url") or "").rstrip("/")
        self.api_key = config.get("api_key") or ""
        self.host_header = config.get("host_header") or ""
        if not self.url:
            raise ValueError("Redmine task source requires 'url' in config")
        if not self.api_key:
            raise ValueError("Redmine task source requires 'api_key' in config")

        server_config = config.get("server")
        if server_config is None:
            server_config = {
                "transport": "stdio",
                "command": "uvx",
                "args": ["--from", "mcp-redmine", "mcp-redmine"],
                "env": {
                    "REDMINE_URL": self.url,
                    "REDMINE_API_KEY": self.api_key,
                },
            }
            if self.host_header:
                server_config["env"]["REDMINE_HOST_HEADER"] = self.host_header

        self._client = McpClient(server_config)
        self._status_ids: dict[str, int] | None = None

    # -- internals ---------------------------------------------------------

    def _call(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        """Invoke ``redmine_request`` on the MCP server and parse the response.

        Returns the parsed YAML payload (typically a dict). The MCP server
        wraps Redmine's JSON in an ``{status_code, body, error}`` envelope and
        returns YAML by default, so we unwrap ``body`` before handing the
        result to callers.
        """
        arguments: dict[str, Any] = {"path": path, "method": method}
        if params is not None:
            # Coerce all params to strings: Redmine expects string query values
            # and the MCP server forwards them as-is.
            arguments["params"] = {k: str(v) for k, v in params.items()}
        if data is not None:
            arguments["data"] = data

        result = self._client.call_tool("redmine_request", arguments=arguments)
        text = _extract_text(result)
        if not text.strip():
            return {}
        parsed = yaml.safe_load(text)
        return _unwrap_envelope(parsed)

    def _resolve_status_id(self, status: str) -> str | int:
        """Resolve a status name to its Redmine numeric id.

        Queries ``/issue_statuses.json`` once and caches the result. Falls back
        to returning the name unchanged if it cannot be resolved (Redmine also
        accepts ``status_id`` as ``open``/``closed`` for filters).
        """
        # Filter values (open/closed/new) are passed through untouched.
        if status in _STATUS_FILTERS:
            return status

        name = _VERDICT_STATUS_NAMES.get(status, status)

        if self._status_ids is None:
            try:
                payload = self._call("GET", "/issue_statuses.json")
                statuses = payload.get("issue_statuses", []) if isinstance(payload, dict) else []
                self._status_ids = {
                    str(s["name"]).lower(): int(s["id"]) for s in statuses if "id" in s and "name" in s
                }
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Could not load Redmine issue statuses: %s", exc)
                self._status_ids = {}

        resolved = self._status_ids.get(name.lower())
        if resolved is not None:
            return resolved
        logger.warning("Redmine status '%s' not found in issue statuses; passing name through", name)
        return name

    def _parse_issue(self, issue: dict[str, Any]) -> Task:
        """Map a Redmine issue dict onto the Task model with rich metadata."""
        issue_id = issue.get("id")
        return Task(
            id=str(issue_id),
            title=issue.get("subject", "") or "",
            description=issue.get("description", "") or "",
            status=((issue.get("status") or {}).get("name") or "open"),
            metadata={
                "project": (issue.get("project") or {}).get("name"),
                "project_id": (issue.get("project") or {}).get("id"),
                "tracker": (issue.get("tracker") or {}).get("name"),
                "priority": (issue.get("priority") or {}).get("name"),
                "assigned_to": (issue.get("assigned_to") or {}).get("name"),
                "author": (issue.get("author") or {}).get("name"),
                "created_on": issue.get("created_on"),
                "updated_on": issue.get("updated_on"),
                "done_ratio": issue.get("done_ratio"),
                "redmine_url": f"{self.url}/issues/{issue_id}" if issue_id is not None else None,
            },
        )

    # -- TaskSource interface ----------------------------------------------

    def fetch_tasks(self, status: str = "open", limit: int = 50) -> list[Task]:
        """Return tasks assigned to the current user with the given status."""
        status_id = self._resolve_status_id(status)
        payload = self._call(
            "GET",
            "/issues.json",
            params={
                "assigned_to_id": "me",
                "status_id": status_id,
                "limit": limit,
            },
        )
        issues = payload.get("issues", []) if isinstance(payload, dict) else []
        return [self._parse_issue(issue) for issue in issues if isinstance(issue, dict)]

    def get_task_details(self, task_id: str) -> Task:
        """Return full task details by Redmine issue id."""
        payload = self._call("GET", f"/issues/{task_id}.json")
        issue = payload.get("issue") if isinstance(payload, dict) else None
        if not issue:
            raise ValueError(f"Task {task_id} not found in Redmine")
        return self._parse_issue(issue)

    def update_task_status(
        self,
        task_id: str,
        status: str,
        comment: str | None = None,
    ) -> None:
        """Update an issue's status (and optionally add a note) in Redmine."""
        status_id = self._resolve_status_id(status)
        issue_payload: dict[str, Any] = {"status_id": status_id}
        if comment:
            issue_payload["notes"] = comment
        self._call("PUT", f"/issues/{task_id}.json", data={"issue": issue_payload})

    def close(self) -> None:
        """Release the MCP client (stops the subprocess server)."""
        self._client.close()
