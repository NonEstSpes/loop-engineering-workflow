"""Unit tests for orphan worktree cleanup on daemon startup."""

from __future__ import annotations

from pathlib import Path

import pytest
from git import Repo

from devflow.daemon.sweep import cleanup_orphan_worktrees


@pytest.fixture
def git_repo_with_orphan(tmp_path: Path) -> Path:
    """Create a repo with a manually-created orphan worktree + branch."""
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

    # Simulate an orphan: create a worktree + branch that no live process owns.
    orphan_dir = tmp_path / "repo-worktree-deadbeef"
    orphan_dir.mkdir(parents=True, exist_ok=True)
    repo.create_head("devflow/4321/deadbeef")
    repo.git.worktree("add", str(orphan_dir), "devflow/4321/deadbeef")

    return repo_path


def test_cleanup_removes_orphan_worktree(git_repo_with_orphan: Path) -> None:
    """cleanup_orphan_worktrees removes orphaned worktree dirs and branches."""
    cleaned = cleanup_orphan_worktrees(git_repo_with_orphan)
    assert len(cleaned) == 1
    assert "deadbeef" in cleaned[0]

    # Worktree dir is gone
    parent = git_repo_with_orphan.parent
    remaining = list(parent.glob("repo-worktree-*"))
    assert remaining == []

    # Branch is gone
    repo = Repo(git_repo_with_orphan)
    branch_names = [b.name for b in repo.branches]
    assert "devflow/4321/deadbeef" not in branch_names


def test_cleanup_no_orphans_is_noop(tmp_path: Path) -> None:
    """A clean repo with no orphan worktrees returns empty list."""
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

    cleaned = cleanup_orphan_worktrees(repo_path)
    assert cleaned == []


def test_cleanup_preserves_non_devflow_worktrees(git_repo_with_orphan: Path) -> None:
    """Worktrees not matching the devflow pattern are left untouched."""
    repo = Repo(git_repo_with_orphan)
    # Add a non-devflow worktree
    other_dir = git_repo_with_orphan.parent / "manual-worktree"
    other_dir.mkdir()
    repo.create_head("feature-branch")
    repo.git.worktree("add", str(other_dir), "feature-branch")

    cleaned = cleanup_orphan_worktrees(git_repo_with_orphan)
    # Only the devflow orphan was cleaned
    assert len(cleaned) == 1
    assert other_dir.exists()
