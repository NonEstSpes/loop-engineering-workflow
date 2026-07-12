"""Workflow runner adapter for the daemon.

Wraps the existing ``run_workflow`` (graph.py:226) so the daemon can run
tasks with:
- asyncio lock coordination (only one task_run at a time);
- event publishing to EventBus (for Phase 5 SSE live updates);
- a synchronous boundary — the daemon's scheduler calls these from a
  thread, and the graph itself is synchronous.

Phase 1 uses ``run_workflow`` (non-interactive). Phase 2 will switch to
``run_workflow_interactive`` with an approval-bridge callback.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from typing import Any

from devflow.config import Config
from devflow.daemon.events import EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.graph import run_workflow
from devflow.mcp.base import TaskSource
from devflow.mcp.factory import build_task_source
from devflow.state import WorkflowState

logger = logging.getLogger(__name__)


class WorkflowRunner:
    """Runs workflow tasks with lock coordination and event publishing."""

    def __init__(
        self,
        app_cfg: Config,
        event_bus: EventBus,
        locks: DaemonLocks,
        task_source: TaskSource | None = None,
    ) -> None:
        self._cfg = app_cfg
        self._bus = event_bus
        self._locks = locks
        self._task_source = task_source
        self.events_published: int = 0

    def run_task(
        self,
        task_id: str,
        repo_path: str,
        thread_id: str | None = None,
    ) -> WorkflowState:
        """Run a single task to completion (non-interactive in Phase 1).

        Publishes a ``task.started`` event before invoking the graph and a
        ``task.finished`` event after. The ``task_run`` lock is exposed via
        :attr:`locks` for the scheduler/orchestrator to acquire around the
        call (Phase 4); this method itself stays synchronous.
        """
        # TODO(Phase 4): acquire locks.task_run() around the graph.invoke call
        # so task runs coordinate with EOD-publish. Phase 1 ships the lock
        # unused; max_instances=1 on the APScheduler job is the only guard.
        topic = f"task.{task_id}"

        self._publish(
            topic,
            {
                "event": "task.started",
                "task_id": task_id,
                "thread_id": thread_id or task_id,
            },
        )

        try:
            final_state = run_workflow(
                app_cfg=self._cfg,
                repo_path=repo_path,
                task_id=task_id,
                task_source=self._task_source,
                thread_id=thread_id or task_id,
            )
            verdict = final_state.get("final_verdict")
            self._publish(
                topic,
                {
                    "event": "task.finished",
                    "task_id": task_id,
                    "verdict": verdict.value if verdict else None,
                },
            )
            return final_state
        except Exception:
            logger.exception("Workflow run failed for task %s", task_id)
            self._publish(
                topic,
                {
                    "event": "task.error",
                    "task_id": task_id,
                    "error": traceback.format_exc(),
                },
            )
            raise

    def run_all(self, repo_path: str, limit: int = 10) -> list[WorkflowState]:
        """Fetch all open tasks and run each to completion.

        Uses the task source from config if none was provided in __init__.
        """
        source = self._task_source
        if source is None:
            source = build_task_source(self._cfg.workflow)
        try:
            tasks = source.fetch_tasks(status="open", limit=limit)
            if not tasks:
                logger.info("No open tasks found.")
                return []

            logger.info("Processing %d open task(s)...", len(tasks))
            results: list[WorkflowState] = []
            for task in tasks:
                logger.info("Task %s: %s", task.id, task.title)
                final_state = self.run_task(
                    task_id=task.id,
                    repo_path=repo_path,
                    thread_id=task.id,
                )
                results.append(final_state)
            return results
        finally:
            if self._task_source is None:
                source.close()

    def _publish(self, topic: str, data: dict[str, Any]) -> None:
        """Publish an event, tracking count for diagnostics.

        Best-effort: a publish failure is logged but never propagated, so
        telemetry cannot break a workflow run. When called from the
        APScheduler thread (no running event loop), a temporary loop is
        created via asyncio.run(). When called from within a running loop
        (Phase 2+ UI triggers), the publish is scheduled as a task.
        """
        self.events_published += 1
        try:
            loop = asyncio.get_running_loop()
            # We are inside a running loop — schedule without blocking.
            loop.create_task(self._bus.publish(topic, data))
        except RuntimeError:
            # No running loop (APScheduler thread) — create one for the call.
            try:
                asyncio.run(self._bus.publish(topic, data))
            except Exception as exc:
                logger.debug("EventBus publish failed for '%s': %s", topic, exc)
