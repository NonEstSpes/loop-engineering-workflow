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


def test_eod_routes_list_pending(
    mock_config: Config, tmp_path
) -> None:
    """GET /api/eod returns pending entries when eod_handler is set."""
    from fastapi.testclient import TestClient

    from devflow.batch.eod_handler import EodHandler
    from devflow.batch.models import BatchEntry
    from devflow.batch.store import BatchStore
    from devflow.daemon.events import EventBus
    from devflow.daemon.locks import DaemonLocks
    from devflow.daemon.web import create_app
    from devflow.schemas import ReporterResponse
    from devflow.state import FinalVerdict

    store = BatchStore(tmp_path / "batch_store.db")
    store.add(
        BatchEntry(
            task_id="T-1",
            task_title="Task 1",
            branch_name="devflow/T-1/abc",
            worktree_path="/p",
            diff="d",
            plan_summary="s",
            plan_steps=[],
            checker_reports=[],
            self_review_notes="",
            final_verdict=FinalVerdict.APPROVE,
            reporter_artifacts=ReporterResponse(
                pr_title="t", pr_description="d", corporate_report="r", commit_message="c"
            ),
            created_at="2026-07-13T10:00:00Z",
        )
    )
    handler = EodHandler(mock_config, store, EventBus(), repo_path=".")
    app = create_app(
        mock_config, DaemonLocks(), EventBus(), eod_handler=handler
    )
    client = TestClient(app)
    resp = client.get("/api/eod")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["task_id"] == "T-1"
    store.close()


def test_eod_publish_route(
    mock_config: Config, tmp_path, monkeypatch
) -> None:
    """POST /api/eod/publish publishes selected entries."""
    from fastapi.testclient import TestClient

    from devflow.batch.eod_handler import EodHandler
    from devflow.batch.models import BatchEntry
    from devflow.batch.store import BatchStore
    from devflow.daemon.events import EventBus
    from devflow.daemon.locks import DaemonLocks
    from devflow.daemon.web import create_app
    from devflow.schemas import ReporterResponse
    from devflow.state import FinalVerdict

    store = BatchStore(tmp_path / "batch_store.db")
    store.add(
        BatchEntry(
            task_id="T-1",
            task_title="Task 1",
            branch_name="devflow/T-1/abc",
            worktree_path="/p",
            diff="d",
            plan_summary="s",
            plan_steps=[],
            checker_reports=[],
            self_review_notes="",
            final_verdict=FinalVerdict.APPROVE,
            reporter_artifacts=ReporterResponse(
                pr_title="t", pr_description="d", corporate_report="r", commit_message="c"
            ),
            created_at="2026-07-13T10:00:00Z",
        )
    )
    handler = EodHandler(mock_config, store, EventBus(), repo_path=".")

    # Stub the publisher so no real forge/httpx calls happen.
    class StubPublisher:
        def publish(self, entry):
            entry.status = "published"
            return entry

    monkeypatch.setattr(handler, "build_publisher", lambda: StubPublisher())

    app = create_app(
        mock_config, DaemonLocks(), EventBus(), eod_handler=handler
    )
    client = TestClient(app)
    resp = client.post("/api/eod/publish", json={"task_ids": ["T-1"]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["published"] == ["T-1"]
    store.close()


def test_eod_finalize_route(
    mock_config: Config, tmp_path
) -> None:
    """POST /api/eod/finalize returns the pending count."""
    from fastapi.testclient import TestClient

    from devflow.batch.eod_handler import EodHandler
    from devflow.batch.models import BatchEntry
    from devflow.batch.store import BatchStore
    from devflow.daemon.events import EventBus
    from devflow.daemon.locks import DaemonLocks
    from devflow.daemon.web import create_app
    from devflow.schemas import ReporterResponse
    from devflow.state import FinalVerdict

    store = BatchStore(tmp_path / "batch_store.db")
    store.add(
        BatchEntry(
            task_id="T-1",
            task_title="t",
            branch_name="b",
            worktree_path="/p",
            diff="d",
            plan_summary="s",
            plan_steps=[],
            checker_reports=[],
            self_review_notes="",
            final_verdict=FinalVerdict.APPROVE,
            reporter_artifacts=ReporterResponse(
                pr_title="t", pr_description="d", corporate_report="r", commit_message="c"
            ),
            created_at="2026-07-13T10:00:00Z",
        )
    )
    handler = EodHandler(mock_config, store, EventBus(), repo_path=".")
    app = create_app(
        mock_config, DaemonLocks(), EventBus(), eod_handler=handler
    )
    client = TestClient(app)
    resp = client.post("/api/eod/finalize")
    assert resp.status_code == 200
    assert resp.json()["pending_count"] == 1
    store.close()


def test_health_includes_batch_store_pending(
    mock_config: Config, tmp_path
) -> None:
    """GET /api/health reports batch_store_pending when eod_handler is set."""
    from fastapi.testclient import TestClient

    from devflow.batch.eod_handler import EodHandler
    from devflow.batch.store import BatchStore
    from devflow.daemon.events import EventBus
    from devflow.daemon.locks import DaemonLocks
    from devflow.daemon.web import create_app

    store = BatchStore(tmp_path / "batch_store.db")
    handler = EodHandler(mock_config, store, EventBus(), repo_path=".")
    app = create_app(
        mock_config, DaemonLocks(), EventBus(), eod_handler=handler
    )
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["batch_store_pending"] == 0
    store.close()
