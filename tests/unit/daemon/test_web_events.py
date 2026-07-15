"""Tests for GET /api/events/history endpoint."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from devflow.batch.event_store import EventStore
from devflow.config import Config, DaemonConfig, WorkflowConfig
from devflow.daemon.events import EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.daemon.web import create_app


def _make_app_with_events(tmp_path: Path) -> tuple:
    store = EventStore(str(tmp_path / "events.db"))
    store.add("task.started", {"event": "task.started", "task_id": "1"})
    store.add("approval.waiting", {"event": "approval.waiting"})
    cfg = Config(workflow=WorkflowConfig(task_source="mock"), providers={}, agents={})
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(
        cfg, locks, bus,
        runner=MagicMock(), scheduler=MagicMock(),
        event_store=store,
    )
    return app, store


def test_get_event_history_returns_entries(tmp_path: Path) -> None:
    app, _ = _make_app_with_events(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/events/history")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    # Newest first.
    assert data[0]["event_type"] == "approval.waiting"


def test_get_event_history_with_filter(tmp_path: Path) -> None:
    app, _ = _make_app_with_events(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/events/history?event_type=task")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["event_type"] == "task.started"


def test_get_event_history_with_limit(tmp_path: Path) -> None:
    app, _ = _make_app_with_events(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/events/history?limit=1")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_get_event_history_no_store_returns_empty(tmp_path: Path) -> None:
    cfg = Config(workflow=WorkflowConfig(task_source="mock"), providers={}, agents={})
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=MagicMock())
    with TestClient(app) as client:
        resp = client.get("/api/events/history")
    assert resp.status_code == 200
    assert resp.json() == []
