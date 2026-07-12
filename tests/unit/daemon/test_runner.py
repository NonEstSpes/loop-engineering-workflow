"""Unit tests for the daemon workflow runner adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from git import Repo

from devflow.config import Config
from devflow.daemon.events import EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.daemon.runner import WorkflowRunner
from devflow.state import FinalVerdict


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository for the maker node."""
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


def test_run_task_completes(
    temp_git_repo: Path,
    mock_config: Config,
    fake_llm_factory: Any,
) -> None:
    """WorkflowRunner.run_task runs a single task to completion."""
    bus = EventBus()
    locks = DaemonLocks()
    runner = WorkflowRunner(mock_config, bus, locks)
    final_state = runner.run_task(
        task_id="MOCK-1",
        repo_path=str(temp_git_repo),
        thread_id="runner-test-1",
    )
    assert final_state.get("final_verdict") == FinalVerdict.APPROVE
    assert final_state.get("error") is None


def test_run_task_publishes_events(
    temp_git_repo: Path,
    mock_config: Config,
    fake_llm_factory: Any,
) -> None:
    """WorkflowRunner publishes node-completion events to EventBus."""
    bus = EventBus()
    locks = DaemonLocks()
    runner = WorkflowRunner(mock_config, bus, locks)
    runner.run_task(
        task_id="MOCK-1",
        repo_path=str(temp_git_repo),
        thread_id="runner-test-2",
    )
    # The runner should have published at least one event to the task topic.
    # We can't easily assert the queue contents after run_task returns
    # (events were consumed by nobody), but we can check that the runner
    # has a non-empty event count.
    assert runner.events_published > 0


def test_run_all_processes_multiple_tasks(
    temp_git_repo: Path,
    mock_config: Config,
    fake_llm_factory: Any,
) -> None:
    """WorkflowRunner.run_all fetches and processes multiple tasks."""
    from devflow.mcp.mock import MockTaskSource

    # Give the mock task source some tasks.
    mock_config.workflow.task_source = "mock"
    bus = EventBus()
    locks = DaemonLocks()
    runner = WorkflowRunner(mock_config, bus, locks, task_source=MockTaskSource({}))
    results = runner.run_all(repo_path=str(temp_git_repo), limit=3)
    # MockTaskSource seeds MOCK-1 and MOCK-2; both should complete.
    assert isinstance(results, list)
    assert len(results) == 2
    for state in results:
        assert state.get("final_verdict") is not None
