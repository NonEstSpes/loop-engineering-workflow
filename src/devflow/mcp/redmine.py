"""Redmine task source adapter."""

from __future__ import annotations

from typing import Any

from devflow.mcp.base import TaskSource
from devflow.state import Task


class RedmineTaskSource(TaskSource):
    """Fetch tasks from Redmine via its REST API or a Redmine MCP server."""

    name = "redmine"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.url = config.get("url", "")
        self.api_key = config.get("api_key", "")
        if not self.url:
            raise ValueError("Redmine task source requires 'url' in config")

    def fetch_tasks(self, status: str = "open", limit: int = 50) -> list[Task]:
        # TODO: integrate with Redmine MCP server or requests
        # This stub returns an empty list; implement real API calls here.
        return []

    def get_task_details(self, task_id: str) -> Task:
        # TODO: fetch issue from Redmine
        raise NotImplementedError("Redmine integration is not implemented yet")

    def update_task_status(
        self,
        task_id: str,
        status: str,
        comment: str | None = None,
    ) -> None:
        # TODO: update issue status and add note
        raise NotImplementedError("Redmine integration is not implemented yet")

    def close(self) -> None:
        pass
