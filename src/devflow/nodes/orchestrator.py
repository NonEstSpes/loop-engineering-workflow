"""Orchestrator node: pick the next task from TODO.md and delegate it.

The orchestrator is the workflow's entry point and its task router. It owns
three responsibilities:

1. **Task selection.** Read ``TODO.md`` (path from ``WorkflowConfig.todo_path``),
   select the topmost actionable entry by priority (``#r0`` highest). If the
   file is missing, generate it from the configured task source first.
2. **Delegation.** Place the selected task into ``state["task"]`` so downstream
   nodes (task_fetcher passthrough, planner, maker, ...) pick it up. Tracker
   references are hydrated with full details via ``get_task_details``.
3. **Subagent oversight** is exercised through the graph's conditional edges
   and checker nodes; this node focuses on getting the right task in front of
   them.

The selected :class:`~devflow.todo.TodoItem` is stored in ``state["todo_item"]``
so the reporter can write the inline result back into the same line.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from devflow.config import Config
from devflow.mcp.base import TaskSource
from devflow.state import Task, WorkflowError, WorkflowState
from devflow.todo import (
    TodoItem,
    ensure_todo,
    generate_todo_from_source,
    mark_in_progress,
    select_next_todo,
)

logger = logging.getLogger(__name__)

# Header written into a freshly generated TODO.md.
_TODO_HEADER = (
    "# TODO\n\n"
    "> Сгенерировано orchestrator'ом из открытых задач трекера.\n"
    "> Приоритеты: #r0 (высший) — #r5 (низший). Строки без #rX игнорируются."
)


def orchestrator_node(
    state: WorkflowState,
    *,
    app_cfg: Config,
    todo_path: str,
    task_source: TaskSource,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Select the next task from ``TODO.md`` and put it into workflow state."""
    # If a task is already in state (e.g. a re-entry during the same pass),
    # delegate immediately without touching the TODO file.
    existing = state.get("task")
    if existing is not None:
        logger.info("Orchestrator received existing task %s", existing.id)
        return {
            "rework_count": 0,
            "checker_reports": [],
            "logs": [f"orchestrator: received existing task {existing.id}"],
        }

    # An explicit task id (--task-id) bypasses TODO selection entirely; the
    # downstream task_fetcher node will load that specific task.
    if task_id is not None:
        logger.info("Orchestrator deferring explicit task_id %s to task_fetcher", task_id)
        return {
            "rework_count": 0,
            "checker_reports": [],
            "logs": [f"orchestrator: deferring explicit task_id {task_id}"],
        }

    path = Path(todo_path)
    try:
        items = ensure_todo(
            path,
            lambda: _factory_from_source(task_source),
            header=_TODO_HEADER,
        )
        if not path.exists():
            # ensure_todo created it just now — log so the user knows.
            logger.info("Orchestrator generated %s (%d tasks)", path, len(items))
    except Exception as exc:
        logger.exception("Orchestrator failed to load/generate TODO")
        return {
            "error": WorkflowError(
                node="orchestrator",
                message=f"Could not read or generate {path}: {exc}",
            ),
            "logs": [f"orchestrator: error loading TODO: {exc}"],
        }

    item = select_next_todo(items)
    if item is None:
        logger.info("No actionable tasks in %s", path)
        return {
            "error": WorkflowError(
                node="orchestrator",
                message=f"No actionable tasks in {path} (need '[ ]' checkbox and a #rX tag)",
            ),
            "logs": ["orchestrator: no actionable tasks"],
        }

    try:
        task = _materialize_task(item, task_source)
        mark_in_progress(path, item)
    except Exception as exc:
        logger.exception("Orchestrator failed to materialize task %s", item.task_id())
        return {
            "error": WorkflowError(
                node="orchestrator",
                message=f"Failed to load task {item.task_id()}: {exc}",
            ),
            "logs": [f"orchestrator: error loading task {item.task_id()}: {exc}"],
        }

    logger.info(
        "Orchestrator selected task %s (priority r%d) from %s",
        task.id,
        item.priority if item.priority is not None else -1,
        path,
    )
    return {
        "task": task,
        "todo_item": item,
        "rework_count": 0,
        "checker_reports": [],
        "logs": [
            f"orchestrator: selected {task.id} (r{item.priority}) from TODO line {item.line_no}",
        ],
    }


def _factory_from_source(task_source: TaskSource) -> list[TodoItem]:
    """Build TODO items from the task source's open tasks."""
    tasks = task_source.fetch_tasks(status="open", limit=50)
    return generate_todo_from_source(tasks)


def _materialize_task(item: TodoItem, task_source: TaskSource) -> Task:
    """Build a :class:`Task` for the selected TODO entry.

    Tracker-referenced tasks are hydrated with full details (description,
    comments) from the source. Free-form local entries become minimal Task
    objects derived from their title.
    """
    if item.is_link:
        return task_source.get_task_details(item.task_ref or "")
    return Task(
        id=item.task_id(),
        title=item.title,
        description=item.title,
        status="open",
        metadata={"source": "todo", "todo_line": item.line_no},
    )
