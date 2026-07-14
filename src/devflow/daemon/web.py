"""FastAPI web app for the daemon.

Phase 1 endpoints:
- ``GET /api/health`` — daemon health (status, scheduler, uptime).
- ``GET /api/state`` — daemon config and HITL strategy.

Phase 2+ will add /api/approvals, /api/tasks/*, /api/eod, /api/events (SSE).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from devflow.batch.eod_handler import EodHandler
from devflow.config import Config
from devflow.daemon.approval_store import ApprovalStore
from devflow.daemon.events import GLOBAL_TOPIC, EventBus
from devflow.daemon.locks import DaemonLocks

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: str
    scheduler: str
    uptime_seconds: int
    current_task: str | None = None
    pending_approvals: int = 0
    batch_store_pending: int = 0
    errors_last_24h: int = 0


class StateResponse(BaseModel):
    hitl_strategy: str
    daemon: dict[str, Any]
    task_source: str


class ApprovalDecision(BaseModel):
    """Body of a POST /api/approvals/{thread_id} decision."""

    approved: bool
    reason: str = ""
    requested_changes: list[str] = Field(default_factory=list)


class EodPublishRequest(BaseModel):
    """Body of POST /api/eod/publish."""

    task_ids: list[str] = Field(default_factory=list)


class TaskCurrentResponse(BaseModel):
    """Active task + current graph node (node is None until runner tracks it)."""

    task_id: str | None = None
    node: str | None = None


class TaskQueueResponse(BaseModel):
    """Pending task queue (introspection limited without a live task source)."""

    queue: list[dict[str, Any]] = Field(default_factory=list)
    note: str = ""


def create_app(
    app_cfg: Config,
    locks: DaemonLocks,
    event_bus: EventBus,
    runner: Any | None = None,
    approval_store: ApprovalStore | None = None,
    eod_handler: EodHandler | None = None,
) -> FastAPI:
    """Create the FastAPI application.

    ``runner`` is the WorkflowRunner (or None in Phase 1 tests). It will be
    used by Phase 2+ endpoints to query task progress and approvals.

    ``approval_store`` is the registry of pending human approvals (Task 4).
    When provided, this exposes ``GET /api/approvals`` and
    ``POST /api/approvals/{thread_id}``, and populates ``pending_approvals``
    in ``GET /api/health``.

    ``eod_handler`` is the EOD batch review/publish orchestrator (Task 5).
    When provided, this exposes the ``/api/eod*`` endpoints and populates
    ``batch_store_pending`` in ``GET /api/health``.
    """
    app = FastAPI(title="devflow-daemon", version="0.1.0")
    start_time = time.monotonic()
    _state: dict[str, Any] = {"current_task": None}

    def _is_task_running() -> bool:
        """Check if the task_run lock is currently held.

        ``asyncio.Lock.locked()`` is a synchronous method and reflects the
        lock's state regardless of which event loop holds it, so this works
        both when called from within the running request loop and from a
        different thread/loop (e.g. a scheduler thread acquiring the lock).
        """
        return locks.task_run().locked()

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        uptime = int(time.monotonic() - start_time)
        task_running = _is_task_running()
        pending = len(approval_store.get_pending()) if approval_store else 0
        batch_pending = 0
        if eod_handler is not None:
            try:
                batch_pending = eod_handler._store.count_pending()  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover - defensive
                logger.debug("Failed to count pending batch entries")
        return HealthResponse(
            status="degraded" if task_running else "healthy",
            scheduler="busy" if task_running else "running",
            uptime_seconds=uptime,
            current_task=_state.get("current_task"),
            pending_approvals=pending,
            batch_store_pending=batch_pending,
        )

    @app.get("/api/state", response_model=StateResponse)
    async def state() -> StateResponse:
        daemon_cfg = app_cfg.workflow.daemon
        return StateResponse(
            hitl_strategy=app_cfg.workflow.hitl_strategy,
            daemon={
                "enabled": daemon_cfg.enabled,
                "task_schedule": daemon_cfg.task_schedule,
                "eod_schedule": daemon_cfg.eod_schedule,
                "port": daemon_cfg.port,
                "approval_timeout_hours": daemon_cfg.approval_timeout_hours,
                "approval_on_timeout": daemon_cfg.approval_on_timeout,
            },
            task_source=app_cfg.workflow.task_source,
        )

    def _entry_summary(entry: Any) -> dict[str, Any]:
        return {
            "id": entry.id,
            "task_id": entry.task_id,
            "task_title": entry.task_title,
            "branch_name": entry.branch_name,
            "final_verdict": entry.final_verdict.value if entry.final_verdict else None,
            "status": entry.status,
            "created_at": entry.created_at,
        }

    @app.get("/api/tasks/current", response_model=TaskCurrentResponse)
    async def tasks_current() -> TaskCurrentResponse:
        return TaskCurrentResponse(
            task_id=_state.get("current_task"),
            node=None,
        )

    @app.get("/api/tasks/queue", response_model=TaskQueueResponse)
    async def tasks_queue() -> TaskQueueResponse:
        return TaskQueueResponse(
            queue=[],
            note="queue introspection not available without an active task source",
        )

    @app.get("/api/tasks/done")
    async def tasks_done() -> list[dict[str, Any]]:
        if eod_handler is None:
            return []
        try:
            entries = eod_handler._store.list_all(status="published")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - defensive
            return []
        return [_entry_summary(e) for e in entries]

    @app.get("/api/tasks/{task_id}")
    async def task_detail(task_id: str) -> dict[str, Any]:
        if eod_handler is None:
            raise HTTPException(status_code=404, detail="No batch store available")
        entries = eod_handler._store.get_by_task(task_id)  # type: ignore[attr-defined]
        if not entries:
            raise HTTPException(status_code=404, detail=f"No entry for task {task_id}")
        # Most recent entry (get_by_task returns oldest-first).
        return entries[-1].model_dump(mode="json")

    @app.get("/api/events")
    async def event_stream() -> EventSourceResponse:
        """Server-Sent Events stream of all daemon events.

        Subscribes to the EventBus global topic and forwards each event as an
        SSE frame (``event: <name>`` + ``data: <json>``). The client connects
        via ``new EventSource('/api/events')``. A 15s ``ping`` heartbeat keeps
        proxies/browsers from closing idle connections.
        """
        queue = await event_bus.subscribe(GLOBAL_TOPIC)

        async def event_generator() -> AsyncGenerator[dict[str, str], None]:
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except TimeoutError:
                        # Heartbeat keepalive (prevents proxy/browser timeouts).
                        yield {"event": "ping", "data": "{}"}
                        continue
                    yield {
                        "event": msg.get("event", "message"),
                        "data": json.dumps(msg),
                    }
            finally:
                # Best-effort cleanup of the subscriber queue.
                pass

        return EventSourceResponse(event_generator())

    if approval_store is not None:

        @app.get("/api/approvals")
        async def list_approvals() -> list[dict[str, Any]]:
            return approval_store.get_pending()

        @app.post("/api/approvals/{thread_id}")
        async def resolve_approval(thread_id: str, decision: ApprovalDecision) -> dict[str, Any]:
            resolved = approval_store.resolve(
                thread_id,
                {
                    "approved": decision.approved,
                    "reason": decision.reason,
                    "requested_changes": decision.requested_changes,
                },
            )
            if not resolved:
                raise HTTPException(
                    status_code=404, detail=f"Unknown thread_id: {thread_id}"
                )
            return {"status": "resolved", "thread_id": thread_id}

    if eod_handler is not None:

        @app.get("/api/eod")
        async def list_eod_pending() -> list[dict[str, Any]]:
            return [_entry_summary(e) for e in eod_handler.list_pending()]

        @app.post("/api/eod/finalize")
        async def finalize_eod() -> dict[str, Any]:
            pending = eod_handler.finalize()
            return {"pending_count": len(pending)}

        @app.post("/api/eod/publish")
        async def publish_eod(req: EodPublishRequest) -> dict[str, Any]:
            return eod_handler.publish_selected(req.task_ids)

        @app.get("/api/eod/entries/{entry_id}")
        async def get_eod_entry(entry_id: int) -> dict[str, Any]:
            entry = eod_handler._store.get_entry(entry_id)  # type: ignore[attr-defined]
            if entry is None:
                raise HTTPException(status_code=404, detail=f"Unknown entry id: {entry_id}")
            return entry.model_dump(mode="json")

    def set_current_task(task_id: str | None) -> None:
        """Allow the runner to report which task is currently active."""
        _state["current_task"] = task_id

    # Expose the setter so the runner can update state.
    app.state.set_current_task = set_current_task  # type: ignore[attr-defined]

    return app


def run_web_server(
    app_cfg: Config,
    locks: DaemonLocks,
    event_bus: EventBus,
    runner: Any | None = None,
    approval_store: ApprovalStore | None = None,
    eod_handler: EodHandler | None = None,
) -> None:
    """Run the uvicorn server (blocking). Called from the daemon entry point.

    ``approval_store`` (Task 4) is forwarded to ``create_app`` so the
    ``/api/approvals`` endpoints can be exposed when the daemon wires it in.

    ``eod_handler`` (Task 5) is forwarded so the ``/api/eod*`` endpoints
    can be exposed when the daemon wires it in.
    """
    import uvicorn

    app = create_app(
        app_cfg, locks, event_bus, runner,
        approval_store=approval_store, eod_handler=eod_handler,
    )
    port = app_cfg.workflow.daemon.port
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
