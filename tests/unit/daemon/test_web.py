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
