"""Unit tests for the daemon FastAPI web app."""

from __future__ import annotations

from fastapi.testclient import TestClient

from devflow.config import Config, WorkflowConfig
from devflow.daemon.events import EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.daemon.web import create_app


def _make_app() -> tuple:
    """Create a test app with minimal config."""
    from devflow.config import DaemonConfig

    cfg = Config(
        workflow=WorkflowConfig(task_source="mock"),
        providers={},
        agents={},
    )
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=None)
    return app, locks


def _make_app_with_store(store):
    """Create a test app wired with an ApprovalStore."""
    from devflow.config import DaemonConfig

    cfg = Config(workflow=WorkflowConfig(task_source="mock"), providers={}, agents={})
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=None, approval_store=store)
    return app, locks


def test_health_endpoint_returns_healthy() -> None:
    """/api/health returns status healthy when no errors."""
    app, _ = _make_app()
    with TestClient(app) as client:
        resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert "scheduler" in data
    assert "uptime_seconds" in data


def test_state_endpoint_returns_config() -> None:
    """/api/state returns daemon config and strategy."""
    app, _ = _make_app()
    with TestClient(app) as client:
        resp = client.get("/api/state")
    assert resp.status_code == 200
    data = resp.json()
    assert data["hitl_strategy"] == "per_plan"
    assert data["daemon"]["enabled"] is True
    assert data["daemon"]["port"] == 8787


def test_health_endpoint_shows_degraded_when_task_running() -> None:
    """/api/health shows degraded status when a task is actively running."""
    import asyncio

    app, locks = _make_app()
    with TestClient(app) as client:
        # Simulate a running task by holding the lock.
        loop = asyncio.new_event_loop()

        async def hold_lock() -> asyncio.Lock:
            lock = locks.task_run()
            await lock.acquire()
            return lock

        lock = loop.run_until_complete(hold_lock())
        try:
            resp = client.get("/api/health")
            data = resp.json()
            assert data["status"] == "degraded"
            assert data["current_task"] is not None or data["scheduler"] == "busy"
        finally:
            lock.release()
            loop.close()


def test_approvals_list_empty() -> None:
    """/api/approvals returns empty list when no approvals pending."""
    from devflow.daemon.approval_store import ApprovalStore

    store = ApprovalStore()
    app, _ = _make_app_with_store(store)
    with TestClient(app) as client:
        resp = client.get("/api/approvals")
    assert resp.status_code == 200
    assert resp.json() == []


def test_approvals_list_shows_pending() -> None:
    """/api/approvals shows registered pending approvals."""
    from devflow.daemon.approval_store import ApprovalStore

    store = ApprovalStore()
    store.register("T-1", {"gate_type": "plan_approval", "task_id": "T-1", "task_title": "Fix bug"})
    app, _ = _make_app_with_store(store)
    with TestClient(app) as client:
        resp = client.get("/api/approvals")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["thread_id"] == "T-1"
    assert data[0]["payload"]["task_title"] == "Fix bug"


def test_approvals_resolve_decision() -> None:
    """POST /api/approvals/{thread_id} delivers a decision to the store."""
    from devflow.daemon.approval_store import ApprovalStore

    store = ApprovalStore()
    store.register("T-1", {"gate_type": "plan_approval", "task_id": "T-1"})
    app, _ = _make_app_with_store(store)
    with TestClient(app) as client:
        resp = client.post(
            "/api/approvals/T-1",
            json={"approved": True, "reason": "looks good", "requested_changes": []},
        )
    assert resp.status_code == 200
    # After resolve, the approval is no longer pending.
    with TestClient(app) as client2:
        resp2 = client2.get("/api/approvals")
    assert resp2.json() == []


def test_approvals_resolve_unknown_returns_404() -> None:
    """POST /api/approvals/{unknown} returns 404."""
    from devflow.daemon.approval_store import ApprovalStore

    store = ApprovalStore()
    app, _ = _make_app_with_store(store)
    with TestClient(app) as client:
        resp = client.post(
            "/api/approvals/nonexistent",
            json={"approved": True, "reason": "", "requested_changes": []},
        )
    assert resp.status_code == 404


def test_health_shows_pending_approvals_count() -> None:
    """/api/health reports the count of pending approvals."""
    from devflow.daemon.approval_store import ApprovalStore

    store = ApprovalStore()
    store.register("T-1", {"task_id": "T-1"})
    store.register("T-2", {"task_id": "T-2"})
    app, _ = _make_app_with_store(store)
    with TestClient(app) as client:
        resp = client.get("/api/health")
    data = resp.json()
    assert data["pending_approvals"] == 2
