"""Task fetcher node: load a task from the configured task source."""

from __future__ import annotations

import logging
from typing import Any

from devflow.mcp.base import TaskSource
from devflow.state import WorkflowError, WorkflowState

logger = logging.getLogger(__name__)


def task_fetcher_node(
    state: WorkflowState,
    *,
    task_source: TaskSource,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Fetch a task from the source and put it into state.

    If the state already contains a task, pass it through unchanged.
    If ``task_id`` is provided, fetch that specific task; otherwise fetch the
    first open task.
    """
    existing = state.get("task")
    if existing is not None:
        return {"logs": [f"task_fetcher: task {existing.id} already in state"]}

    try:
        if task_id is not None:
            task = task_source.get_task_details(task_id)
            logger.info("Fetched task %s by ID", task.id)
            return {
                "task": task,
                "logs": [f"task_fetcher: fetched task {task.id} by ID"],
            }

        tasks = task_source.fetch_tasks(status="open", limit=1)
        if not tasks:
            return {
                "error": WorkflowError(
                    node="task_fetcher",
                    message="No open tasks found in source",
                ),
                "logs": ["task_fetcher: no open tasks found"],
            }
        task = tasks[0]
        logger.info("Fetched first open task %s", task.id)
        return {
            "task": task,
            "logs": [f"task_fetcher: fetched first open task {task.id}"],
        }
    except Exception as exc:
        logger.exception("Task fetch failed")
        return {
            "error": WorkflowError(node="task_fetcher", message=str(exc)),
            "logs": [f"task_fetcher: error {exc}"],
        }
