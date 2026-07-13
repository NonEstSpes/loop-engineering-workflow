"""End-to-end integration tests for HITL strategies.

Tests the full flow: graph build -> interrupt -> bridge -> store -> resolve
for each strategy. Uses fake LLM and mock task source (no real API calls).
"""

from __future__ import annotations

import copy
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from git import Repo

from devflow.config import Config, HitlStrategy
from devflow.daemon.approval_bridge import ApprovalBridge
from devflow.daemon.approval_store import ApprovalStore
from devflow.daemon.events import EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.daemon.runner import WorkflowRunner
from devflow.state import FinalVerdict


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


def _auto_approve_in_bg(store: ApprovalStore) -> threading.Thread:
    """Background thread that auto-approves any pending approval."""
    def _approve() -> None:
        for _ in range(100):
            pending = store.get_pending()
            if pending:
                for entry in pending:
                    store.resolve(
                        entry["thread_id"],
                        {"approved": True, "reason": "auto", "requested_changes": []},
                    )
                return
            time.sleep(0.05)

    t = threading.Thread(target=_approve, daemon=True)
    t.start()
    return t


def test_per_plan_strategy_completes(
    temp_git_repo: Path,
    mock_config: Config,
    fake_llm_factory: Any,
) -> None:
    """per_plan: plan_approval interrupts, publish_approval auto-approves."""
    cfg = copy.deepcopy(mock_config)
    cfg.workflow.hitl_strategy = HitlStrategy.PER_PLAN
    cfg.workflow.human_in_the_loop = True

    store = ApprovalStore()
    bridge = ApprovalBridge(
        store=store,
        push_channels=[],
        approval_timeout_hours=1,
        on_timeout="defer",
    )
    _auto_approve_in_bg(store)

    runner = WorkflowRunner(cfg, EventBus(), DaemonLocks(), approval_bridge=bridge)
    final_state = runner.run_task("MOCK-1", str(temp_git_repo), "per-plan-e2e")

    assert final_state.get("final_verdict") == FinalVerdict.APPROVE


def test_full_detail_strategy_completes(
    temp_git_repo: Path,
    mock_config: Config,
    fake_llm_factory: Any,
) -> None:
    """full_detail: plan auto-approves, publish_approval interrupts."""
    cfg = copy.deepcopy(mock_config)
    cfg.workflow.hitl_strategy = HitlStrategy.FULL_DETAIL
    cfg.workflow.human_in_the_loop = True

    store = ApprovalStore()
    bridge = ApprovalBridge(
        store=store,
        push_channels=[],
        approval_timeout_hours=1,
        on_timeout="defer",
    )
    _auto_approve_in_bg(store)

    runner = WorkflowRunner(cfg, EventBus(), DaemonLocks(), approval_bridge=bridge)
    final_state = runner.run_task("MOCK-1", str(temp_git_repo), "full-detail-e2e")

    assert final_state.get("final_verdict") == FinalVerdict.APPROVE


def test_end_of_day_strategy_completes_no_interrupt(
    temp_git_repo: Path,
    mock_config: Config,
    fake_llm_factory: Any,
) -> None:
    """end_of_day: no interrupts at all (both gates auto-approve)."""
    cfg = copy.deepcopy(mock_config)
    cfg.workflow.hitl_strategy = HitlStrategy.END_OF_DAY
    cfg.workflow.human_in_the_loop = True

    store = ApprovalStore()
    bridge = ApprovalBridge(
        store=store,
        push_channels=[],
        approval_timeout_hours=1,
        on_timeout="defer",
    )

    runner = WorkflowRunner(cfg, EventBus(), DaemonLocks(), approval_bridge=bridge)
    final_state = runner.run_task("MOCK-1", str(temp_git_repo), "eod-e2e")

    assert final_state.get("final_verdict") == FinalVerdict.APPROVE
    # No approval should have been registered (both gates auto-approve).
    assert store.get_pending() == []
