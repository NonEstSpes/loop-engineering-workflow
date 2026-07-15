"""Tests for /api/queue/* endpoints."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from devflow.batch.queue_store import QueueEntry, QueueStore
from devflow.config import Config, DaemonConfig, WorkflowConfig
from devflow.daemon.events import EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.daemon.web import create_app


def _make_app_with_queue(tmp_path: Path) -> tuple:
    store = QueueStore(str(tmp_path / "queue.db"))
    store.set_queue([
        QueueEntry(position=0, task_id="1", task_title="A", priority=0),
        QueueEntry(position=1, task_id="2", task_title="B", priority=1),
        QueueEntry(position=2, task_id="3", task_title="C", priority=3),
    ])
    cfg = Config(workflow=WorkflowConfig(task_source="mock"), providers={}, agents={})
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(
        cfg, locks, bus,
        runner=MagicMock(), scheduler=MagicMock(),
        queue_store=store,
    )
    return app, store


def test_get_queue_returns_ordered_list(tmp_path: Path) -> None:
    app, _ = _make_app_with_queue(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/queue")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    assert data[0]["task_id"] == "1"
    assert data[0]["position"] == 0


def test_reorder_moves_task(tmp_path: Path) -> None:
    app, _ = _make_app_with_queue(tmp_path)
    with TestClient(app) as client:
        resp = client.patch("/api/queue/reorder", json={"task_id": "3", "new_position": 0})
    assert resp.status_code == 200
    data = resp.json()
    assert [e["task_id"] for e in data] == ["3", "1", "2"]


def test_move_up(tmp_path: Path) -> None:
    app, _ = _make_app_with_queue(tmp_path)
    with TestClient(app) as client:
        resp = client.post("/api/queue/move-up", json={"task_id": "2"})
    assert resp.status_code == 200
    assert [e["task_id"] for e in resp.json()] == ["2", "1", "3"]


def test_move_down(tmp_path: Path) -> None:
    app, _ = _make_app_with_queue(tmp_path)
    with TestClient(app) as client:
        resp = client.post("/api/queue/move-down", json={"task_id": "1"})
    assert resp.status_code == 200
    assert [e["task_id"] for e in resp.json()] == ["2", "1", "3"]


def test_reorder_unknown_task_404(tmp_path: Path) -> None:
    app, _ = _make_app_with_queue(tmp_path)
    with TestClient(app) as client:
        resp = client.patch("/api/queue/reorder", json={"task_id": "bogus", "new_position": 0})
    assert resp.status_code == 404


def test_reorder_invalid_position_422(tmp_path: Path) -> None:
    app, _ = _make_app_with_queue(tmp_path)
    with TestClient(app) as client:
        resp = client.patch("/api/queue/reorder", json={"task_id": "1", "new_position": 99})
    assert resp.status_code == 422
