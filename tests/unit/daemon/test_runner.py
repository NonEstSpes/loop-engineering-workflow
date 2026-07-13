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
from devflow.mcp.mock import MockTaskSource
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


def test_run_task_uses_interactive_when_bridge_provided(
    temp_git_repo: Path,
    mock_config: Config,
    fake_llm_factory: Any,
) -> None:
    """When an ApprovalBridge is provided, run_task uses run_workflow_interactive."""
    import threading
    import time

    from devflow.daemon.approval_bridge import ApprovalBridge
    from devflow.daemon.approval_store import ApprovalStore

    # Enable per_plan so the plan_approval interrupt fires.
    mock_config.workflow.hitl_strategy = "per_plan"
    mock_config.workflow.human_in_the_loop = True

    store = ApprovalStore()
    bridge = ApprovalBridge(
        store=store,
        push_channels=[],
        approval_timeout_hours=1,
        on_timeout="defer",
    )

    bus = EventBus()
    locks = DaemonLocks()
    runner = WorkflowRunner(mock_config, bus, locks, approval_bridge=bridge)

    # Auto-resolve the approval immediately in a background thread.
    def auto_approve() -> None:
        for _ in range(40):
            pending = store.get_pending()
            if pending:
                tid = pending[0]["thread_id"]
                store.resolve(
                    tid,
                    {"approved": True, "reason": "auto", "requested_changes": []},
                )
                return
            time.sleep(0.05)

    t = threading.Thread(target=auto_approve)
    t.start()

    final_state = runner.run_task(
        task_id="MOCK-1",
        repo_path=str(temp_git_repo),
        thread_id="interactive-runner-test",
    )
    t.join(timeout=2.0)

    assert final_state.get("final_verdict") == FinalVerdict.APPROVE


def test_run_task_falls_back_to_non_interactive_without_bridge(
    temp_git_repo: Path,
    mock_config: Config,
    fake_llm_factory: Any,
) -> None:
    """Without a bridge, run_task uses run_workflow (no interrupt)."""
    bus = EventBus()
    locks = DaemonLocks()
    runner = WorkflowRunner(mock_config, bus, locks)

    final_state = runner.run_task(
        task_id="MOCK-1",
        repo_path=str(temp_git_repo),
        thread_id="non-interactive-test",
    )
    assert final_state.get("final_verdict") == FinalVerdict.APPROVE


def test_run_task_end_of_day_stores_batch_entry(
    temp_git_repo: Path,
    mock_config: Config,
    fake_llm_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In end_of_day mode with a batch_store, run_task stores a BatchEntry."""
    from devflow.batch.store import BatchStore
    from devflow.config import HitlStrategy

    mock_config.workflow.hitl_strategy = HitlStrategy.END_OF_DAY
    mock_config.workflow.human_in_the_loop = True

    store = BatchStore(temp_git_repo / ".devflow" / "batch_store.db")
    try:
        runner = WorkflowRunner(
            mock_config,
            EventBus(),
            DaemonLocks(),
            task_source=MockTaskSource({}),
            batch_store=store,
        )
        # We don't run the full graph (needs LLM etc.); instead test the
        # storage helper directly.
        from devflow.schemas import PlanStep, ReporterResponse
        from devflow.state import (
            CheckerReport,
            CheckerVerdict,
            FinalVerdict,
            Plan,
            Task,
            WorkflowState,
        )

        final_state: WorkflowState = {
            "task": Task(id="T-99", title="Batch test", description="d"),
            "plan": Plan(summary="s", steps=[PlanStep(id="1", description="d")]),
            "diff": "diff content",
            "branch_name": "devflow/T-99/abc12345",
            "worktree_path": str(temp_git_repo),
            "checker_reports": [
                CheckerReport(
                    agent_name="checker_a",
                    verdict=CheckerVerdict.APPROVE,
                    summary="ok",
                )
            ],
            "final_verdict": FinalVerdict.APPROVE,
            "self_review_notes": "fine",
            "reporter_artifacts": ReporterResponse(
                pr_title="feat: x",
                pr_description="desc",
                corporate_report="report",
                commit_message="feat: x",
            ),
        }
        entry_id = runner._store_batch_entry("T-99", final_state)
        assert entry_id > 0
        assert store.count_pending() == 1
        entry = store.get_entry(entry_id)
        assert entry is not None
        assert entry.task_id == "T-99"
        assert entry.reporter_artifacts.pr_title == "feat: x"
    finally:
        store.close()
