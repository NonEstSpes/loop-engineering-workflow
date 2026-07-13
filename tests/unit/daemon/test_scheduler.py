"""Unit tests for the daemon APScheduler configuration."""

from __future__ import annotations

from pathlib import Path

import pytest
from git import Repo

from devflow.config import Config
from devflow.daemon.events import EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.daemon.runner import WorkflowRunner
from devflow.daemon.scheduler import DaemonScheduler


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    repo = Repo.init(repo_path)
    with repo.config_writer() as writer:
        writer.set_value("user", "name", "Test")
        writer.set_value("user", "email", "test@example.com")
    (repo_path / "README.md").write_text("# init", encoding="utf-8")
    repo.git.add("--all")
    repo.index.commit("init")
    if "main" not in repo.heads:
        repo.create_head("main")
    return repo_path


def test_scheduler_starts_and_stops(mock_config: Config) -> None:
    """DaemonScheduler starts and stops cleanly."""
    bus = EventBus()
    locks = DaemonLocks()
    runner = WorkflowRunner(mock_config, bus, locks)
    scheduler = DaemonScheduler(mock_config, runner)
    scheduler.start()
    assert scheduler.is_running
    scheduler.shutdown()
    assert not scheduler.is_running


def test_scheduler_registers_task_job(
    mock_config: Config,
    temp_git_repo: Path,
) -> None:
    """register_jobs adds a task-run job with the configured cron schedule."""
    bus = EventBus()
    locks = DaemonLocks()
    runner = WorkflowRunner(mock_config, bus, locks)
    scheduler = DaemonScheduler(mock_config, runner)
    scheduler.start()
    scheduler.register_jobs(str(temp_git_repo))
    assert scheduler.job_count >= 1
    scheduler.shutdown()


def test_scheduler_registers_eod_job_when_eod_mode(
    mock_config: Config,
    temp_git_repo: Path,
) -> None:
    """register_jobs adds an EOD job when hitl_strategy is end_of_day."""
    import copy

    cfg = copy.deepcopy(mock_config)
    cfg.workflow.hitl_strategy = "end_of_day"
    cfg.workflow.daemon.enabled = True

    bus = EventBus()
    locks = DaemonLocks()
    runner = WorkflowRunner(cfg, bus, locks)
    scheduler = DaemonScheduler(cfg, runner)
    scheduler.start()
    scheduler.register_jobs(str(temp_git_repo))
    # In end_of_day mode, both task and eod jobs should be registered.
    assert scheduler.job_count >= 2
    scheduler.shutdown()


def test_eod_wrapper_calls_handler_when_provided(
    mock_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_run_eod_wrapper calls finalize + publish_selected when a handler is set."""
    mock_config.workflow.hitl_strategy = "end_of_day"
    mock_config.workflow.daemon.enabled = True

    finalize_called: list[bool] = []
    publish_called: list[list[str]] = []

    class FakeEodHandler:
        def finalize(self):
            finalize_called.append(True)
            return []

        def publish_selected(self, task_ids):
            publish_called.append(task_ids)
            return {"published": [], "failed": [], "skipped": []}

    runner = WorkflowRunner(mock_config, EventBus(), DaemonLocks())
    scheduler = DaemonScheduler(mock_config, runner, eod_handler=FakeEodHandler())

    scheduler._run_eod_wrapper(repo_path=".")

    assert finalize_called == [True]
    assert publish_called == [[]]  # publish all pending


def test_eod_wrapper_without_handler_is_noop(
    mock_config: Config,
) -> None:
    """_run_eod_wrapper without a handler logs and does not raise."""
    mock_config.workflow.hitl_strategy = "end_of_day"
    runner = WorkflowRunner(mock_config, EventBus(), DaemonLocks())
    scheduler = DaemonScheduler(mock_config, runner)  # no eod_handler
    scheduler._run_eod_wrapper(repo_path=".")  # must not raise
