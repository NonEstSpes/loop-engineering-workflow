"""Workflow runner adapter for the daemon.

Wraps the existing ``run_workflow`` (graph.py:226) so the daemon can run
tasks with:
- asyncio lock coordination (only one task_run at a time);
- event publishing to EventBus (for Phase 5 SSE live updates);
- a synchronous boundary — the daemon's scheduler calls these from a
  thread, and the graph itself is synchronous.

When an ``ApprovalBridge`` is provided, ``run_task`` uses
``run_workflow_interactive`` (resuming from human-approval interrupts via
the bridge's callback). Without a bridge, it falls back to the
non-interactive ``run_workflow``.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from collections.abc import Callable
from typing import Any

from devflow.batch.models import BatchEntry
from devflow.batch.store import BatchStore
from devflow.config import Config, HitlStrategy
from devflow.daemon.approval_bridge import ApprovalBridge
from devflow.daemon.events import EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.graph import run_workflow, run_workflow_interactive
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
        approval_bridge: ApprovalBridge | None = None,
        batch_store: BatchStore | None = None,
        on_task_change: Callable[[str | None], None] | None = None,
    ) -> None:
        self._cfg = app_cfg
        self._bus = event_bus
        self._locks = locks
        self._task_source = task_source
        self._bridge = approval_bridge
        self._batch_store = batch_store
        self._on_task_change = on_task_change
        self.events_published: int = 0

    @property
    def locks(self) -> DaemonLocks:
        """Expose locks for scheduler/API coordination."""
        return self._locks

    def run_task(
        self,
        task_id: str,
        repo_path: str,
        thread_id: str | None = None,
    ) -> WorkflowState:
        """Run a single task to completion.

        When an ``ApprovalBridge`` was supplied to ``__init__``, the
        interactive runner (``run_workflow_interactive``) is used so the
        workflow can pause on human-approval interrupts and resume via the
        bridge's callback. Otherwise the non-interactive ``run_workflow``
        is used (any interrupt simply ends the run).

        Publishes a ``task.started`` event before invoking the graph and a
        ``task.finished`` event after. The ``task_run`` lock is exposed via
        :attr:`locks` for the scheduler/orchestrator to acquire around the
        call (Phase 4); this method itself stays synchronous.
        """
        # Concurrency: relies on APScheduler max_instances=1; no cross-loop lock (see HANDOFF.md).
        topic = f"task.{task_id}"

        if self._on_task_change is not None:
            self._on_task_change(task_id)

        self._publish(
            topic,
            {
                "event": "task.started",
                "task_id": task_id,
                "thread_id": thread_id or task_id,
            },
        )

        try:
            if self._bridge is not None:
                callback = self._bridge.build_callback()
                final_state = run_workflow_interactive(
                    app_cfg=self._cfg,
                    repo_path=repo_path,
                    task_id=task_id,
                    task_source=self._task_source,
                    thread_id=thread_id or task_id,
                    approval_callback=callback,
                )
            else:
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
            # In end_of_day mode, accumulate the result into the batch store.
            if (
                self._batch_store is not None
                and self._cfg.workflow.hitl_strategy == HitlStrategy.END_OF_DAY
            ):
                try:
                    self._store_batch_entry(task_id, final_state)
                except Exception:
                    logger.exception("Failed to store batch entry for task %s", task_id)
            if self._on_task_change is not None:
                self._on_task_change(None)
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
            if self._on_task_change is not None:
                self._on_task_change(None)
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

    def _store_batch_entry(self, task_id: str, state: WorkflowState) -> int:
        """Build a ``BatchEntry`` from ``state`` and persist it.

        Called after each per-task run in end_of_day mode. Reads the
        reporter artifacts (always populated by ``reporter_node``), the
        plan, diff, checker reports, and branch from the final state.
        """
        from datetime import UTC, datetime

        assert self._batch_store is not None

        task = state.get("task")
        artifacts = state.get("reporter_artifacts")
        if task is None or artifacts is None:
            logger.warning(
                "Cannot store batch entry for %s: missing task or artifacts",
                task_id,
            )
            return -1

        plan = state.get("plan")
        plan_steps = [s.description for s in plan.steps] if plan else []
        plan_summary = plan.summary if plan else ""

        entry = BatchEntry(
            task_id=task.id,
            task_title=task.title,
            branch_name=state.get("branch_name") or "",
            worktree_path=state.get("worktree_path") or "",
            diff=state.get("diff") or "",
            plan_summary=plan_summary,
            plan_steps=plan_steps,
            checker_reports=state.get("checker_reports") or [],
            self_review_notes=state.get("self_review_notes") or "",
            final_verdict=state.get("final_verdict"),
            reporter_artifacts=artifacts,
            created_at=datetime.now(UTC).isoformat(),
        )
        return self._batch_store.add(entry)

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
