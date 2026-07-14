"""Tests for dashboard control endpoints (run tasks, todo, config, agents)."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from devflow.config import AgentConfig, Config, DaemonConfig, WorkflowConfig
from devflow.daemon.events import EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.daemon.web import create_app


def _make_control_app(
    cfg: Config | None = None,
    runner: MagicMock | None = None,
    scheduler: MagicMock | None = None,
) -> tuple:
    """Create an app wired with runner + scheduler for control endpoints."""
    if cfg is None:
        cfg = Config(
            workflow=WorkflowConfig(task_source="mock"),
            providers={},
            agents={
                "planner": AgentConfig(
                    name="planner",
                    provider="mock",
                    model="mock-model",
                    temperature=0.3,
                    system_prompt="You are the planner.",
                ),
            },
        )
        cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(
        cfg, locks, bus,
        runner=runner or MagicMock(),
        scheduler=scheduler or MagicMock(),
    )
    return app, cfg


def test_app_state_exposes_runner_scheduler_cfg() -> None:
    """app.state has runner, scheduler, cfg, _run_lock attached."""
    app, cfg = _make_control_app()
    assert app.state.runner is not None
    assert app.state.scheduler is not None
    assert app.state.cfg is cfg
    assert isinstance(app.state._run_lock, type(threading.Lock()))


def test_create_app_accepts_scheduler_param() -> None:
    """create_app signature accepts scheduler=None without error."""
    cfg = Config(workflow=WorkflowConfig(task_source="mock"), providers={}, agents={})
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    # scheduler=None must not raise.
    app = create_app(cfg, locks, bus, runner=None, scheduler=None)
    assert app.state.scheduler is None


# ---------------------------------------------------------------------------
# POST /api/tasks/run
# ---------------------------------------------------------------------------

import time  # noqa: E402


def test_run_task_returns_202_with_run_id() -> None:
    """POST /api/tasks/run returns 202 with a run_id when idle."""
    runner = MagicMock()
    runner.run_task.return_value = {}
    app, _ = _make_control_app(runner=runner)
    with TestClient(app) as client:
        resp = client.post("/api/tasks/run", json={"task_id": "12345"})
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "started"
    assert "run_id" in data
    assert data["task_id"] == "12345"


def test_run_task_returns_409_when_busy() -> None:
    """POST /api/tasks/run returns 409 when _run_lock is held."""
    runner = MagicMock()
    app, _ = _make_control_app(runner=runner)
    with TestClient(app) as client:
        # Hold the lock to simulate a running task.
        app.state._run_lock.acquire()
        try:
            resp = client.post("/api/tasks/run", json={"task_id": "12345"})
        finally:
            app.state._run_lock.release()
    assert resp.status_code == 409
    assert "already running" in resp.json()["detail"].lower()


def test_run_task_runs_in_background_thread() -> None:
    """The handler returns 202 immediately without blocking on run_task."""
    started = threading.Event()

    def slow_run(task_id, repo_path, thread_id=None):
        started.set()
        time.sleep(0.2)
        return {}

    runner = MagicMock()
    runner.run_task.side_effect = slow_run
    app, _ = _make_control_app(runner=runner)
    with TestClient(app) as client:
        resp = client.post("/api/tasks/run", json={"task_id": "12345"})
        # Response arrives before the slow run finishes.
        assert resp.status_code == 202
        # But the run eventually starts in the background.
        assert started.wait(timeout=2.0)


def test_run_task_without_task_id_calls_run_all() -> None:
    """No task_id → runner.run_all is used instead of run_task."""
    runner = MagicMock()
    runner.run_all.return_value = []
    app, _ = _make_control_app(runner=runner)
    with TestClient(app) as client:
        resp = client.post("/api/tasks/run", json={})
    assert resp.status_code == 202
    runner.run_all.assert_called_once()
    runner.run_task.assert_not_called()

