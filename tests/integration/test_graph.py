"""Integration tests for the full workflow graph."""

from __future__ import annotations

from pathlib import Path

import pytest
from git import Repo

from devflow.graph import run_workflow
from devflow.state import FinalVerdict, Task


@pytest.fixture
def temp_git_repo(temp_dir: Path) -> Path:
    """Create a temporary git repository for the maker node."""
    repo_path = temp_dir / "repo"
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


def test_workflow_happy_path(
    temp_git_repo: Path,
    mock_config: object,
    fake_llm_factory: object,
) -> None:
    """The full graph runs end-to-end with fake LLMs and produces a final verdict."""
    final_state = run_workflow(
        app_cfg=mock_config,
        repo_path=str(temp_git_repo),
        task_id="MOCK-1",
        thread_id="test-thread",
    )

    assert final_state.get("task") is not None
    assert isinstance(final_state["task"], Task)
    assert final_state["task"].id == "MOCK-1"

    assert final_state.get("plan") is not None
    assert final_state.get("diff") is not None
    assert "hello.py" in final_state["diff"]

    reports = final_state.get("checker_reports", [])
    assert len(reports) == 3
    assert all(r.verdict.value == "approve" for r in reports)

    assert final_state.get("final_verdict") == FinalVerdict.APPROVE
    assert final_state.get("error") is None
    assert final_state.get("pr_url") is not None or final_state.get("report_url") is not None
