"""Mock task source for local development and tests."""

from __future__ import annotations

from typing import Any

from devflow.mcp.base import TaskSource
from devflow.state import Task


class MockTaskSource(TaskSource):
    """Returns canned tasks without external dependencies."""

    name = "mock"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._tasks: dict[str, Task] = {
            "MOCK-1": Task(
                id="MOCK-1",
                title="Add hello world endpoint",
                description="Create a simple /hello endpoint that returns JSON.",
                status="open",
            ),
            "MOCK-2": Task(
                id="MOCK-2",
                title="Refactor config loader",
                description="Move configuration loading into a dedicated module with validation.",
                status="open",
            ),
        }

    def fetch_tasks(self, status: str = "open", limit: int = 50) -> list[Task]:
        return [task for task in self._tasks.values() if task.status == status][:limit]

    def get_task_details(self, task_id: str) -> Task:
        if task_id not in self._tasks:
            raise ValueError(f"Task {task_id} not found in mock source")
        return self._tasks[task_id]

    def update_task_status(
        self,
        task_id: str,
        status: str,
        comment: str | None = None,
    ) -> None:
        if task_id in self._tasks:
            self._tasks[task_id].status = status

    def close(self) -> None:
        pass
