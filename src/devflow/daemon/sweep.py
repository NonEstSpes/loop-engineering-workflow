"""Startup cleanup of orphaned git worktrees.

When the daemon crashes or is killed mid-workflow, the worktree directory
and local branch created by ``GitWorktreeManager`` (tools/git_worktree.py:54-61)
are left behind. This module finds and removes them on daemon startup so
the next run starts clean.

Only ``devflow/{task_id}/{uuid}`` branches and ``{repo}-worktree-{uuid}``
directories are touched — other worktrees are preserved.
"""

from __future__ import annotations

import logging
from pathlib import Path

from git import Repo

logger = logging.getLogger(__name__)

# Matches the branch pattern in git_worktree.py:53: devflow/{task_id}/{uuid}
_DEVFLOW_BRANCH_PREFIX = "devflow/"
# Matches the worktree dir pattern in git_worktree.py:54-57: {repo}-worktree-{uuid}
_DEVFLOW_WORKTREE_SUFFIX = "-worktree-"


def cleanup_orphan_worktrees(repo_path: str | Path) -> list[str]:
    """Remove orphaned devflow worktrees and branches for ``repo_path``.

    Scans sibling directories of ``repo_path`` for worktree dirs matching
    ``{repo_name}-worktree-{uuid}`` and removes both the worktree and its
    ``devflow/{task_id}/{uuid}`` branch from the repo.

    Returns a list of cleaned worktree directory paths (as strings).
    """
    repo_path = Path(repo_path)
    repo = Repo(repo_path)
    cleaned: list[str] = []

    # Find worktree dirs in the parent directory matching the devflow pattern.
    parent = repo_path.parent
    repo_name = repo_path.name
    pattern = f"{repo_name}{_DEVFLOW_WORKTREE_SUFFIX}"

    orphan_dirs = sorted(parent.glob(f"{pattern}*"))

    for orphan_dir in orphan_dirs:
        if not orphan_dir.is_dir():
            continue
        _remove_orphan(repo, orphan_dir)
        cleaned.append(str(orphan_dir))

    # Also clean up any devflow branches whose worktrees are already gone.
    _remove_dangling_devflow_branches(repo)

    return cleaned


def _remove_orphan(repo: Repo, orphan_dir: Path) -> None:
    """Remove a single orphan worktree dir and its branch."""
    try:
        repo.git.worktree("remove", str(orphan_dir), "--force")
    except Exception as exc:
        logger.warning("Failed to git-remove worktree %s: %s; deleting dir", orphan_dir, exc)
        # Fall back to manual directory removal if git worktree remove fails
        import shutil

        shutil.rmtree(orphan_dir, ignore_errors=True)

    # Find and delete the associated branch (devflow/{task_id}/{uuid}).
    # The uuid is the last path component of the worktree dir, after the last '-'.
    dir_name = orphan_dir.name
    uuid_part = dir_name.rsplit("-", 1)[-1] if "-" in dir_name else ""
    if uuid_part:
        for branch in list(repo.branches):
            if branch.name.startswith(_DEVFLOW_BRANCH_PREFIX) and branch.name.endswith(uuid_part):
                try:
                    repo.delete_head(branch.name, force=True)
                    logger.info("Removed orphan branch: %s", branch.name)
                except Exception as exc:
                    logger.warning("Failed to delete orphan branch %s: %s", branch.name, exc)


def _remove_dangling_devflow_branches(repo: Repo) -> None:
    """Remove devflow/* branches that have no associated worktree."""
    try:
        worktree_output = repo.git.worktree("list", "--porcelain")
    except Exception as exc:
        logger.warning("Failed to list worktrees: %s", exc)
        return

    # Parse worktree list to find which branches still have live worktrees.
    live_branches: set[str] = set()
    for line in worktree_output.splitlines():
        if line.startswith("branch "):
            # Format: "branch refs/heads/devflow/4321/deadbeef"
            ref = line[len("branch ") :].strip()
            if ref.startswith("refs/heads/"):
                live_branches.add(ref[len("refs/heads/") :])

    for branch in list(repo.branches):
        if (
            branch.name.startswith(_DEVFLOW_BRANCH_PREFIX)
            and branch.name not in live_branches
        ):
            try:
                repo.delete_head(branch.name, force=True)
                logger.info("Removed dangling devflow branch: %s", branch.name)
            except Exception as exc:
                logger.warning("Failed to delete dangling branch %s: %s", branch.name, exc)
