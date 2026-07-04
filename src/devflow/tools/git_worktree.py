"""Git worktree helpers for isolated maker execution."""

from __future__ import annotations

import logging
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, cast

from git import Repo

logger = logging.getLogger(__name__)


class GitWorktreeManager:
    """Manage an isolated git worktree for the maker agent."""

    def __init__(self, repo_path: Path | str, base_branch: str = "main") -> None:
        self.repo_path = Path(repo_path).resolve()
        self.base_branch = base_branch
        self.repo = Repo(self.repo_path)
        self._worktree_path: Path | None = None
        self._branch_name: str | None = None

    @property
    def worktree_path(self) -> Path:
        if self._worktree_path is None:
            raise RuntimeError("Worktree has not been created yet.")
        return self._worktree_path

    @property
    def branch_name(self) -> str:
        if self._branch_name is None:
            raise RuntimeError("Worktree has not been created yet.")
        return self._branch_name

    def create(self, task_id: str) -> Path:
        """Create a new worktree and branch for the given task."""
        # Ensure base branch exists locally
        if self.base_branch not in self.repo.heads:
            try:
                origin = self.repo.remotes.origin
                origin.fetch(refspec=f"{self.base_branch}:{self.base_branch}")
            except Exception:
                pass
        if self.base_branch in self.repo.heads:
            base_ref = self.repo.heads[self.base_branch]
        else:
            base_ref = cast(Any, self.repo.head)

        self._branch_name = f"devflow/{task_id}/{uuid.uuid4().hex[:8]}"
        worktree_dir = (
            self.repo_path.parent / f"{self.repo_path.name}-worktree-{uuid.uuid4().hex[:8]}"
        )
        worktree_dir.mkdir(parents=True, exist_ok=True)

        # Create branch and worktree
        self.repo.create_head(self._branch_name, base_ref)
        self.repo.git.worktree("add", str(worktree_dir), self._branch_name)

        self._worktree_path = worktree_dir
        logger.info("Created worktree %s on branch %s", worktree_dir, self._branch_name)
        return worktree_dir

    def configure_user(self, name: str, email: str) -> None:
        """Configure git user for commits in the worktree."""
        wt_repo = Repo(self.worktree_path)
        wt_repo.config_writer().set_value("user", "name", name).release()
        wt_repo.config_writer().set_value("user", "email", email).release()

    def add_and_commit(self, message: str) -> None:
        """Stage all changes and commit them."""
        wt_repo = Repo(self.worktree_path)
        wt_repo.git.add("--all")
        if wt_repo.is_dirty(untracked_files=True):
            wt_repo.index.commit(message)
            logger.info("Committed changes: %s", message)
        else:
            logger.info("No changes to commit")

    def get_diff(self, target: str | None = None) -> str:
        """Return diff between worktree branch and target branch."""
        target = target or self.base_branch
        wt_repo = Repo(self.worktree_path)
        try:
            return str(wt_repo.git.diff(f"{target}...{self._branch_name}"))
        except Exception:
            # Fallback to diff against merge-base
            return str(wt_repo.git.diff(target))

    def run_command(
        self,
        command: list[str],
        cwd: Path | None = None,
        check: bool = True,
    ) -> str:
        """Run a shell command inside the worktree."""
        cwd = cwd or self.worktree_path
        logger.debug("Running command in worktree: %s", " ".join(command))
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        output = f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        if check and result.returncode != 0:
            raise RuntimeError(f"Command failed ({result.returncode}): {command}\n{output}")
        return output

    def cleanup(self) -> None:
        """Remove the worktree and delete the branch."""
        if self._worktree_path is None:
            return
        try:
            self.repo.git.worktree("remove", str(self._worktree_path), "--force")
        except Exception as exc:
            logger.warning("Failed to remove worktree via git: %s", exc)
            shutil.rmtree(self._worktree_path, ignore_errors=True)

        if self._branch_name and self._branch_name in self.repo.heads:
            try:
                self.repo.delete_head(self._branch_name, force=True)
            except Exception as exc:
                logger.warning("Failed to delete branch %s: %s", self._branch_name, exc)

        self._worktree_path = None
        self._branch_name = None

    def __enter__(self) -> GitWorktreeManager:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.cleanup()
