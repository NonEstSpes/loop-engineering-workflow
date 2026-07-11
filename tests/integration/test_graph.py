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


def test_workflow_driven_by_todo(
    temp_git_repo: Path,
    mock_config: object,
    fake_llm_factory: object,
    tmp_path: Path,
) -> None:
    """The orchestrator picks a task from TODO.md and the reporter marks it done.

    No --task-id is passed, so the orchestrator must read TODO.md, select the
    topmost entry by priority, hydrate it from the mock source, run the graph,
    and have the reporter write the inline result back into the same line.
    """
    todo_path = tmp_path / "TODO.md"
    todo_path.write_text(
        "- [ ] #r2 [#MOCK-2] — Lower priority\n"
        "- [ ] #r0 [#MOCK-1] — Immediate\n",
        encoding="utf-8",
    )
    mock_config.workflow.todo_path = str(todo_path)  # type: ignore[union-attr]

    final_state = run_workflow(
        app_cfg=mock_config,
        repo_path=str(temp_git_repo),
        thread_id="todo-thread",
    )

    # The r0 entry (MOCK-1) wins over r2.
    task = final_state.get("task")
    assert isinstance(task, Task)
    assert task.id == "MOCK-1"
    assert final_state.get("final_verdict") == FinalVerdict.APPROVE
    assert final_state.get("todo_item") is not None

    # The reporter updated the originating TODO line in place.
    text = todo_path.read_text(encoding="utf-8")
    assert "- [x]" in text
    assert "✅ done:" in text
    # The lower-priority line stays untouched.
    assert "- [ ] #r2 [#MOCK-2] — Lower priority" in text
