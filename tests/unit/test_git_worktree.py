"""Unit tests for the git worktree manager."""

from __future__ import annotations

from pathlib import Path

import pytest
from git import Repo

from devflow.tools.git_worktree import GitWorktreeManager


@pytest.fixture
def temp_repo(temp_dir: Path) -> Path:
    """Create a temporary git repository with a main branch."""
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


def test_create_worktree(temp_repo: Path) -> None:
    """A worktree can be created on a new branch."""
    manager = GitWorktreeManager(temp_repo, base_branch="main")
    worktree_path = manager.create("TASK-1")
    assert worktree_path.exists()
    assert (worktree_path / "README.md").exists()
    manager.cleanup()


def test_commit_and_diff(temp_repo: Path) -> None:
    """Changes in the worktree can be committed and diffed against the base branch."""
    with GitWorktreeManager(temp_repo, base_branch="main") as manager:
        worktree_path = manager.create("TASK-2")
        manager.configure_user("Dev", "dev@example.com")
        (worktree_path / "feature.txt").write_text("feature", encoding="utf-8")
        manager.add_and_commit("add feature")
        diff = manager.get_diff()
        assert "feature.txt" in diff


def test_cleanup_removes_worktree(temp_repo: Path) -> None:
    """Cleanup removes the worktree directory and branch."""
    manager = GitWorktreeManager(temp_repo, base_branch="main")
    worktree_path = manager.create("TASK-3")
    branch_name = manager.branch_name
    manager.cleanup()
    assert not worktree_path.exists()
    repo = Repo(temp_repo)
    assert branch_name not in repo.heads
