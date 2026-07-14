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
