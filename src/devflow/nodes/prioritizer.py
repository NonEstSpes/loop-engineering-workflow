"""LLM prioritizer node — evaluates TASKS.md and writes the execution queue.

Inserted before the orchestrator in the graph. Reads TASKS.md, asks the LLM
for an optimal execution order (considering priorities, dependencies, code
context), and writes the result to QueueStore. On failure, the queue stays
empty and the orchestrator falls back to ``select_next_todo``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from devflow.batch.queue_store import QueueEntry, QueueStore
from devflow.config import Config
from devflow.llm_factory import build_llm
from devflow.schemas import PrioritizationResult
from devflow.todo import parse_todo
from devflow.utils.structured_llm import call_structured

logger = logging.getLogger(__name__)


def prioritizer_node(
    state: dict[str, Any],
    *,
    app_cfg: Config,
    repo_path: str,
    queue_store: QueueStore,
    event_bus: Any | None = None,
) -> dict[str, Any]:
    """Evaluate TASKS.md via LLM and write the execution queue.

    This node does NOT select a task (that's the orchestrator's job). It only
    writes the ordered queue so the orchestrator can pick ``next_task_id()``.
    """
    todo_path = Path(app_cfg.workflow.todo_path)
    if not todo_path.exists():
        logger.info("Prioritizer: no TASKS.md at %s, skipping", todo_path)
        return {"logs": ["prioritizer: no TASKS.md, skipped"]}

    items = parse_todo(todo_path)
    # Only actionable tasks: checkbox [ ] + has a task ref.
    candidates = [
        it for it in items
        if it.checkbox == "[ ]" and it.task_ref is not None
    ]
    if not candidates:
        logger.info("Prioritizer: no actionable tasks in TASKS.md")
        return {"logs": ["prioritizer: no actionable tasks"]}

    # Build the LLM prompt input: task list with priorities.
    task_lines = []
    for it in candidates:
        prio = f"r{it.priority}" if it.priority is not None else "none"
        task_lines.append(f"- [{prio}] #{it.task_ref} — {it.title}")

    agent_cfg = app_cfg.agents.get("prioritizer")
    if agent_cfg is None:
        logger.warning("Prioritizer: no 'prioritizer' agent configured, skipping")
        return {"logs": ["prioritizer: no agent configured, skipped"]}

    llm = build_llm(agent_cfg, app_cfg)
    user_prompt = (
        f"Tasks to prioritize:\n" + "\n".join(task_lines) + "\n\n"
        f"Determine the optimal execution order. Return ordered task IDs."
    )

    try:
        result: PrioritizationResult = call_structured(
            llm, agent_cfg.system_prompt, user_prompt, PrioritizationResult
        )
    except Exception as exc:
        logger.exception("Prioritizer LLM call failed")
        return {
            "logs": [
                f"prioritizer: failed ({exc}), orchestrator will use select_next_todo"
            ]
        }

    # Write the queue: map LLM-ordered task_ids back to TodoItems for metadata.
    item_map = {it.task_ref: it for it in candidates}
    entries: list[QueueEntry] = []
    for pt in result.ordered_tasks:
        it = item_map.get(pt.task_id)
        entries.append(
            QueueEntry(
                position=0,  # set_queue assigns real positions
                task_id=pt.task_id,
                task_title=it.title if it else "",
                priority=it.priority if it else None,
                reason=pt.reason,
                updated_at="",
            )
        )
    # Also append any tasks the LLM missed (safety net).
    seen = {pt.task_id for pt in result.ordered_tasks}
    for it in candidates:
        if it.task_ref not in seen:
            entries.append(
                QueueEntry(
                    position=0,
                    task_id=it.task_ref or "",
                    task_title=it.title,
                    priority=it.priority,
                    reason="not evaluated by LLM",
                    updated_at="",
                )
            )

    queue_store.set_queue(entries)
    logger.info("Prioritizer wrote %d tasks to queue", len(entries))

    # Publish SSE event so the dashboard refreshes.
    if event_bus is not None:
        import asyncio

        try:
            asyncio.run(
                event_bus.publish(
                    "*", {"event": "queue.updated", "count": len(entries)}
                )
            )
        except RuntimeError:
            pass

    return {"logs": [f"prioritizer: wrote {len(entries)} tasks to queue"]}
