"""FastAPI web app for the daemon.

Phase 1 endpoints:
- ``GET /api/health`` — daemon health (status, scheduler, uptime).
- ``GET /api/state`` — daemon config and HITL strategy.

Phase 2+ will add /api/approvals, /api/tasks/*, /api/eod, /api/events (SSE).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from devflow.config import Config
from devflow.daemon.approval_store import ApprovalStore
from devflow.daemon.events import EventBus
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


def create_app(
    app_cfg: Config,
    locks: DaemonLocks,
    event_bus: EventBus,
    runner: Any | None = None,
    approval_store: ApprovalStore | None = None,
) -> FastAPI:
    """Create the FastAPI application.

    ``runner`` is the WorkflowRunner (or None in Phase 1 tests). It will be
    used by Phase 2+ endpoints to query task progress and approvals.

    ``approval_store`` is the registry of pending human approvals (Task 4).
    When provided, this exposes ``GET /api/approvals`` and
    ``POST /api/approvals/{thread_id}``, and populates ``pending_approvals``
    in ``GET /api/health``.
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
        return HealthResponse(
            status="degraded" if task_running else "healthy",
            scheduler="busy" if task_running else "running",
            uptime_seconds=uptime,
            current_task=_state.get("current_task"),
            pending_approvals=pending,
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
) -> None:
    """Run the uvicorn server (blocking). Called from the daemon entry point.

    ``approval_store`` (Task 4) is forwarded to ``create_app`` so the
    ``/api/approvals`` endpoints can be exposed when the daemon wires it in.
    """
    import uvicorn

    app = create_app(app_cfg, locks, event_bus, runner, approval_store=approval_store)
    port = app_cfg.workflow.daemon.port
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
