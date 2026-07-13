"""Abstract base class for forge backends (GitHub, GitLab, etc.).

Mirrors the plug-in pattern used by :class:`devflow.mcp.base.TaskSource`
and :class:`devflow.notifications.base.NotificationChannel`: a config dict,
abstract ``push`` and ``create_mr``, optional ``healthcheck``/``close``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class MRInfo(BaseModel):
    """Result of creating a merge request / pull request."""

    url: str
    number: int | None = None


class ForgeBackend(ABC):
    """Adapter that pushes branches and creates merge requests on a forge.

    ``push`` pushes a local branch to the remote. ``create_mr`` opens a
    merge request / pull request. Both return identifying info.
    """

    name: str = "base"

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    @abstractmethod
    def push(self, branch: str, target: str, repo_path: str) -> str:
        """Push ``branch`` to the remote. Returns the pushed commit SHA."""

    @abstractmethod
    def create_mr(
        self, branch: str, target: str, title: str, description: str
    ) -> MRInfo:
        """Create a merge request from ``branch`` into ``target``.

        Returns an :class:`MRInfo` with the MR URL and optional number.
        If an MR already exists for this branch, return the existing one
        (idempotent).
        """

    def healthcheck(self) -> bool:
        """Return True when the backend is ready (token set, repo reachable)."""
        return True

    def close(self) -> None:  # noqa: B027 - optional hook
        """Release any resources (HTTP clients)."""
