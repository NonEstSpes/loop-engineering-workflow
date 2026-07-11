"""Unit tests for run_workflow_interactive (interrupt + resume consumer)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from git import Repo

from devflow.config import Config
from devflow.graph import run_workflow, run_workflow_interactive
from devflow.state import FinalVerdict, Task


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


def _enable_human_in_loop(mock_config: Config) -> Config:
    """Return a copy of mock_config with human_in_the_loop=True and auto_approve off."""
    import copy

    cfg = copy.deepcopy(mock_config)
    cfg.workflow.human_in_the_loop = True
    # Ensure plan_approval is NOT auto-approved so the interrupt fires.
    approval = cfg.agents.get("plan_approval")
    if approval is not None:
        approval.auto_approve = False
    return cfg


# ---------------------------------------------------------------------------
# Interactive runner resumes from interrupt
# ---------------------------------------------------------------------------


def test_interactive_resumes_on_plan_approval(
    temp_git_repo: Path,
    mock_config: object,
    fake_llm_factory: object,
) -> None:
    """The interactive runner detects the plan-approval interrupt and resumes."""
    cfg = _enable_human_in_loop(mock_config)

    callback_calls: list[dict[str, Any]] = []

    def approval_callback(payload: dict[str, Any], state: Any) -> dict[str, Any]:
        callback_calls.append(payload)
        return {"approved": True, "reason": "ok", "requested_changes": []}

    final_state = run_workflow_interactive(
        app_cfg=cfg,
        repo_path=str(temp_git_repo),
        task_id="MOCK-1",
        thread_id="interactive-thread",
        approval_callback=approval_callback,
    )

    # The callback was invoked with the plan-approval payload.
    assert len(callback_calls) == 1
    assert "plan_summary" in callback_calls[0]
    assert callback_calls[0]["task_id"] == "MOCK-1"

    # And the workflow ran to completion after the resume.
    assert final_state.get("final_verdict") == FinalVerdict.APPROVE
    assert final_state.get("error") is None


def test_interactive_without_callback_returns_paused(
    temp_git_repo: Path,
    mock_config: object,
    fake_llm_factory: object,
) -> None:
    """Without a callback the runner returns the paused state (no crash)."""
    cfg = _enable_human_in_loop(mock_config)

    final_state = run_workflow_interactive(
        app_cfg=cfg,
        repo_path=str(temp_git_repo),
        task_id="MOCK-1",
        thread_id="paused-thread",
        approval_callback=None,
    )

    # The graph paused at plan_approval; the task and plan exist but no verdict.
    assert final_state.get("task") is not None
    assert final_state.get("plan") is not None
    assert final_state.get("final_verdict") is None


def test_interactive_rejection_routes_to_reporter(
    temp_git_repo: Path,
    mock_config: object,
    fake_llm_factory: object,
) -> None:
    """A rejection via the callback routes straight to the reporter."""
    cfg = _enable_human_in_loop(mock_config)

    def reject_callback(payload: dict[str, Any], state: Any) -> dict[str, Any]:
        return {"approved": False, "reason": "no", "requested_changes": []}

    final_state = run_workflow_interactive(
        app_cfg=cfg,
        repo_path=str(temp_git_repo),
        task_id="MOCK-1",
        thread_id="reject-thread",
        approval_callback=reject_callback,
    )

    # Rejected plans skip the maker; the reporter runs but no verdict is set.
    assert final_state.get("task") is not None
    assert final_state.get("diff") is None


# ---------------------------------------------------------------------------
# Interactive runner is a no-op when there is no interrupt (auto-approve)
# ---------------------------------------------------------------------------


def test_interactive_auto_approve_no_interrupt(
    temp_git_repo: Path,
    mock_config: object,
    fake_llm_factory: object,
) -> None:
    """When human_in_the_loop is off, no interrupt fires and the callback is unused."""
    # mock_config has human_in_the_loop=False and plan_approval auto_approve=True.
    callback_calls: list[dict[str, Any]] = []

    def approval_callback(payload: dict[str, Any], state: Any) -> dict[str, Any]:
        callback_calls.append(payload)
        return {"approved": True, "reason": "", "requested_changes": []}

    final_state = run_workflow_interactive(
        app_cfg=mock_config,
        repo_path=str(temp_git_repo),
        task_id="MOCK-1",
        thread_id="auto-thread",
        approval_callback=approval_callback,
    )

    assert callback_calls == []
    assert final_state.get("final_verdict") == FinalVerdict.APPROVE


def test_run_workflow_still_works(
    temp_git_repo: Path,
    mock_config: object,
    fake_llm_factory: object,
) -> None:
    """The non-interactive runner is unaffected by the new code."""
    final_state = run_workflow(
        app_cfg=mock_config,
        repo_path=str(temp_git_repo),
        task_id="MOCK-1",
        thread_id="plain-thread",
    )
    assert final_state.get("final_verdict") == FinalVerdict.APPROVE
    assert isinstance(final_state.get("task"), Task)
