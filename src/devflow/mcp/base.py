"""Abstract base for task source adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from devflow.state import Task


class TaskSource(ABC):
    """Adapter that fetches tasks from an external tracker via MCP or API."""

    name: str = "base"

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    @abstractmethod
    def fetch_tasks(self, status: str = "open", limit: int = 50) -> list[Task]:
        """Return a list of tasks to process."""

    @abstractmethod
    def get_task_details(self, task_id: str) -> Task:
        """Return full task details by ID."""

    def update_task_status(
        self,
        task_id: str,
        status: str,
        comment: str | None = None,
    ) -> None:
        """Optionally update task status in the tracker."""
        raise NotImplementedError(f"{self.name} does not support status updates")

    @abstractmethod
    def close(self) -> None:
        """Release any resources (connections, clients)."""
        pass
