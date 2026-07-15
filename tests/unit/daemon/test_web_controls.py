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


# ---------------------------------------------------------------------------
# GET/PATCH /api/todo
# ---------------------------------------------------------------------------

_TODO = """\
# TODO
- [ ] #r2 [#251977](https://example.com/251977) — Fix the bug
- [ ] #r1 — Urgent fix
"""


def _make_app_with_todo(tmp_path: Path) -> tuple:
    todo_path = tmp_path / "TODO.md"
    todo_path.write_text(_TODO, encoding="utf-8")
    cfg = Config(
        workflow=WorkflowConfig(task_source="mock", todo_path=str(todo_path)),
        providers={},
        agents={},
    )
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=MagicMock())
    return app, todo_path


def test_get_todo_returns_entries(tmp_path: Path) -> None:
    app, _ = _make_app_with_todo(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/todo")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 2
    task_entry = next(d for d in data if d["checkbox"] == "[ ]")
    assert task_entry["priority"] is not None


def test_patch_todo_changes_priority(tmp_path: Path) -> None:
    app, todo_path = _make_app_with_todo(tmp_path)
    with TestClient(app) as client:
        resp = client.patch("/api/todo/2", json={"priority": 0})
    assert resp.status_code == 200
    assert resp.json()["priority"] == 0
    # Persisted to disk.
    assert "#r0" in todo_path.read_text(encoding="utf-8")


def test_patch_todo_changes_status(tmp_path: Path) -> None:
    app, _ = _make_app_with_todo(tmp_path)
    with TestClient(app) as client:
        resp = client.patch("/api/todo/2", json={"status": "done"})
    assert resp.status_code == 200
    assert resp.json()["checkbox"] == "[x]"


def test_patch_todo_missing_line_404(tmp_path: Path) -> None:
    app, _ = _make_app_with_todo(tmp_path)
    with TestClient(app) as client:
        resp = client.patch("/api/todo/999", json={"priority": 0})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET/PATCH/diff/save /api/config
# ---------------------------------------------------------------------------

def test_get_config_returns_all_fields() -> None:
    cfg = Config(
        workflow=WorkflowConfig(task_source="mock", hitl_strategy="per_plan"),
        providers={},
        agents={},
    )
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=MagicMock())
    with TestClient(app) as client:
        resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["hitl_strategy"] == "per_plan"
    assert data["daemon"]["task_schedule"] == "0 9,15 * * 1-5"
    assert data["daemon"]["port"] == 8787


def test_patch_config_mutates_hitl_in_memory() -> None:
    cfg = Config(
        workflow=WorkflowConfig(task_source="mock"),
        providers={},
        agents={},
    )
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    sched = MagicMock()
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=sched)
    with TestClient(app) as client:
        resp = client.patch("/api/config", json={"hitl_strategy": "full_detail"})
    assert resp.status_code == 200
    assert cfg.workflow.hitl_strategy == "full_detail"


def test_patch_config_rejects_restart_only_field() -> None:
    cfg = Config(workflow=WorkflowConfig(task_source="mock"), providers={}, agents={})
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=MagicMock())
    with TestClient(app) as client:
        resp = client.patch("/api/config", json={"daemon": {"port": 9999}})
    assert resp.status_code == 422
    assert "restart" in resp.json()["detail"].lower()


def test_patch_config_reschedules_on_schedule_change() -> None:
    cfg = Config(workflow=WorkflowConfig(task_source="mock"), providers={}, agents={})
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    sched = MagicMock()
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=sched)
    with TestClient(app) as client:
        resp = client.patch(
            "/api/config",
            json={"daemon": {"task_schedule": "*/30 * * * *"}},
        )
    assert resp.status_code == 200
    # reschedule is called with the new schedule; the real DaemonScheduler
    # mutates cfg.workflow.daemon.task_schedule internally. With a mock the
    # field stays at its old value — that's expected.
    sched.reschedule.assert_called_once()
    sched.reschedule.assert_called_with(
        task_schedule="*/30 * * * *", eod_schedule=None
    )


def test_patch_config_invalid_cron_returns_422() -> None:
    cfg = Config(workflow=WorkflowConfig(task_source="mock"), providers={}, agents={})
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    sched = MagicMock()
    sched.reschedule.side_effect = ValueError("bad cron")
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=sched)
    with TestClient(app) as client:
        resp = client.patch(
            "/api/config",
            json={"daemon": {"task_schedule": "bad"}},
        )
    assert resp.status_code == 422


def test_save_config_writes_yaml_to_disk(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    wf_path = config_dir / "workflow.yaml"
    cfg = Config(
        workflow=WorkflowConfig(task_source="mock", hitl_strategy="full_detail"),
        providers={},
        agents={},
    )
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=MagicMock())
    app.state.config_dir = str(config_dir)
    with TestClient(app) as client:
        resp = client.post("/api/config/save")
    assert resp.status_code == 200
    assert wf_path.exists()
    written = wf_path.read_text(encoding="utf-8")
    assert "full_detail" in written


def test_config_diff_shows_unsaved_changes(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "workflow.yaml").write_text(
        "task_source: mock\nhitl_strategy: per_plan\n", encoding="utf-8"
    )
    cfg = Config(
        workflow=WorkflowConfig(task_source="mock", hitl_strategy="full_detail"),
        providers={},
        agents={},
    )
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=MagicMock())
    app.state.config_dir = str(config_dir)
    with TestClient(app) as client:
        resp = client.get("/api/config/diff")
    assert resp.status_code == 200
    data = resp.json()
    assert data["clean"] is False
    changed_fields = [c["field"] for c in data["changed"]]
    assert "hitl_strategy" in changed_fields


# ---------------------------------------------------------------------------
# PUT /api/config/hitl
# ---------------------------------------------------------------------------

def test_put_hitl_switches_strategy() -> None:
    cfg = Config(
        workflow=WorkflowConfig(task_source="mock", hitl_strategy="per_plan"),
        providers={},
        agents={},
    )
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    sched = MagicMock()
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=sched)
    with TestClient(app) as client:
        resp = client.put("/api/config/hitl", json={"strategy": "end_of_day"})
    assert resp.status_code == 200
    assert cfg.workflow.hitl_strategy == "end_of_day"
    # Switching TO end_of_day enables the EOD job.
    sched.set_eod_job.assert_called_once_with(enabled=True, repo_path=".")


def test_put_hitl_switching_away_disables_eod() -> None:
    cfg = Config(
        workflow=WorkflowConfig(task_source="mock", hitl_strategy="end_of_day"),
        providers={},
        agents={},
    )
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    sched = MagicMock()
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=sched)
    with TestClient(app) as client:
        resp = client.put("/api/config/hitl", json={"strategy": "per_plan"})
    assert resp.status_code == 200
    sched.set_eod_job.assert_called_once_with(enabled=False, repo_path=".")


def test_put_hitl_invalid_strategy_422() -> None:
    cfg = Config(workflow=WorkflowConfig(task_source="mock"), providers={}, agents={})
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=MagicMock())
    with TestClient(app) as client:
        resp = client.put("/api/config/hitl", json={"strategy": "bogus"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET/PUT/POST /api/agents
# ---------------------------------------------------------------------------

def _make_app_with_agents(tmp_path: Path) -> tuple:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "planner.md").write_text(
        "---\nname: planner\nprovider: mock\nmodel: mock-model\ntemperature: 0.3\n---\n\nYou are the planner.\n",
        encoding="utf-8",
    )
    cfg = Config(
        workflow=WorkflowConfig(task_source="mock"),
        providers={},
        agents={
            "planner": AgentConfig(
                name="planner", provider="mock", model="mock-model",
                temperature=0.3, system_prompt="You are the planner.",
            ),
        },
    )
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=MagicMock())
    app.state.config_dir = str(tmp_path)
    return app, tmp_path


def test_get_agents_returns_list(tmp_path: Path) -> None:
    app, _ = _make_app_with_agents(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "planner"
    assert data[0]["provider"] == "mock"


def test_get_agent_detail(tmp_path: Path) -> None:
    app, _ = _make_app_with_agents(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/agents/planner")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "planner"
    assert data["system_prompt"] == "You are the planner."


def test_get_agent_unknown_404(tmp_path: Path) -> None:
    app, _ = _make_app_with_agents(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/agents/bogus")
    assert resp.status_code == 404


def test_put_agent_prompt_mutates_in_memory(tmp_path: Path) -> None:
    app, _ = _make_app_with_agents(tmp_path)
    with TestClient(app) as client:
        resp = client.put(
            "/api/agents/planner/prompt",
            json={"system_prompt": "You are a better planner."},
        )
    assert resp.status_code == 200
    # In-memory mutation takes effect immediately.
    assert app.state.cfg.agents["planner"].system_prompt == "You are a better planner."


def test_save_agent_writes_md_to_disk(tmp_path: Path) -> None:
    app, tmp = _make_app_with_agents(tmp_path)
    with TestClient(app) as client:
        client.put(
            "/api/agents/planner/prompt",
            json={"system_prompt": "You are a saved planner."},
        )
        resp = client.post("/api/agents/planner/save")
    assert resp.status_code == 200
    written = (tmp / "agents" / "planner.md").read_text(encoding="utf-8")
    assert "You are a saved planner." in written
    assert "name: planner" in written


