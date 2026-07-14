"""Tests for /api/tasks/* routes."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from devflow.batch.eod_handler import EodHandler
from devflow.batch.models import BatchEntry, BatchStatus
from devflow.batch.store import BatchStore
from devflow.config import Config
from devflow.daemon.events import EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.daemon.web import create_app
from devflow.schemas import ReporterResponse
from devflow.state import FinalVerdict


def _make_entry(task_id: str, status: str = BatchStatus.PUBLISHED) -> BatchEntry:
    return BatchEntry(
        task_id=task_id,
        task_title=f"Task {task_id}",
        branch_name=f"devflow/{task_id}/abc",
        worktree_path="/tmp/repo-wt",
        diff="diff",
        plan_summary="s",
        plan_steps=["step 1"],
        checker_reports=[],
        self_review_notes="",
        final_verdict=FinalVerdict.APPROVE,
        reporter_artifacts=ReporterResponse(
            pr_title="t", pr_description="d", corporate_report="r", commit_message="c"
        ),
        status=status,
        created_at="2026-07-13T10:00:00Z",
    )


def test_tasks_current_returns_none_initially(mock_config: Config) -> None:
    """GET /api/tasks/current returns null task_id when nothing is running."""
    bus = EventBus()
    app = create_app(mock_config, DaemonLocks(), bus)
    client = TestClient(app)
    resp = client.get("/api/tasks/current")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] is None
    assert data["node"] is None


def test_tasks_current_reflects_set_current_task(mock_config: Config) -> None:
    """GET /api/tasks/current reflects what set_current_task was called with."""
    bus = EventBus()
    app = create_app(mock_config, DaemonLocks(), bus)
    app.state.set_current_task("T-42")  # type: ignore[attr-defined]
    client = TestClient(app)
    resp = client.get("/api/tasks/current")
    assert resp.status_code == 200
    assert resp.json()["task_id"] == "T-42"


def test_tasks_done_returns_published_entries(
    mock_config: Config, tmp_path: Path
) -> None:
    """GET /api/tasks/done returns published batch entries."""
    store = BatchStore(tmp_path / "batch_store.db")
    store.add(_make_entry("T-1"))
    store.add(_make_entry("T-2"))
    handler = EodHandler(mock_config, store, EventBus(), repo_path=".")
    app = create_app(mock_config, DaemonLocks(), EventBus(), eod_handler=handler)
    client = TestClient(app)
    resp = client.get("/api/tasks/done")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert {e["task_id"] for e in data} == {"T-1", "T-2"}
    store.close()


def test_tasks_done_empty_when_no_eod_handler(mock_config: Config) -> None:
    """GET /api/tasks/done returns [] when no eod_handler is wired."""
    app = create_app(mock_config, DaemonLocks(), EventBus())
    client = TestClient(app)
    resp = client.get("/api/tasks/done")
    assert resp.status_code == 200
    assert resp.json() == []


def test_tasks_detail_returns_entry_by_task_id(
    mock_config: Config, tmp_path: Path
) -> None:
    """GET /api/tasks/{task_id} returns the most recent entry for that task."""
    store = BatchStore(tmp_path / "batch_store.db")
    store.add(_make_entry("T-1"))
    handler = EodHandler(mock_config, store, EventBus(), repo_path=".")
    app = create_app(mock_config, DaemonLocks(), EventBus(), eod_handler=handler)
    client = TestClient(app)
    resp = client.get("/api/tasks/T-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "T-1"
    assert data["reporter_artifacts"]["pr_title"] == "t"
    store.close()


def test_tasks_detail_404_for_unknown(mock_config: Config, tmp_path: Path) -> None:
    """GET /api/tasks/{task_id} returns 404 when no entry exists."""
    store = BatchStore(tmp_path / "batch_store.db")
    handler = EodHandler(mock_config, store, EventBus(), repo_path=".")
    app = create_app(mock_config, DaemonLocks(), EventBus(), eod_handler=handler)
    client = TestClient(app)
    resp = client.get("/api/tasks/T-UNKNOWN")
    assert resp.status_code == 404
    store.close()


def test_tasks_queue_returns_empty_note(mock_config: Config) -> None:
    """GET /api/tasks/queue returns an empty list with a note."""
    app = create_app(mock_config, DaemonLocks(), EventBus())
    client = TestClient(app)
    resp = client.get("/api/tasks/queue")
    assert resp.status_code == 200
    data = resp.json()
    assert data["queue"] == []
