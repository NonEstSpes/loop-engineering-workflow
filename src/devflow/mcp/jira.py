"""Jira task source adapter."""

from __future__ import annotations

from typing import Any

from devflow.mcp.base import TaskSource
from devflow.state import Task


class JiraTaskSource(TaskSource):
    """Fetch tasks from Jira via its REST API or a Jira MCP server."""

    name = "jira"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.url = config.get("url", "")
        self.username = config.get("username", "")
        self.api_token = config.get("api_token", "")
        if not self.url:
            raise ValueError("Jira task source requires 'url' in config")

    def fetch_tasks(self, status: str = "open", limit: int = 50) -> list[Task]:
        # TODO: integrate with Jira MCP server or requests
        # This stub returns an empty list; implement real API calls here.
        return []

    def get_task_details(self, task_id: str) -> Task:
        # TODO: fetch issue from Jira
        raise NotImplementedError("Jira integration is not implemented yet")

    def update_task_status(
        self,
        task_id: str,
        status: str,
        comment: str | None = None,
    ) -> None:
        # TODO: transition issue and add comment
        raise NotImplementedError("Jira integration is not implemented yet")

    def close(self) -> None:
        pass
