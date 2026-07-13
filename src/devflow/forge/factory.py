"""Factory for building ForgeBackend adapters from workflow configuration.

Mirrors :mod:`devflow.mcp.factory` and :mod:`devflow.notifications.factory`:
a registry mapping provider names to ``ForgeBackend`` subclasses, a
``build_forge_backend`` builder, and a ``register_forge_backend`` hook.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from devflow.config import WorkflowConfig
from devflow.forge.base import ForgeBackend
from devflow.forge.github import GitHubBackend
from devflow.forge.gitlab import GitLabBackend

logger = logging.getLogger(__name__)

_FORGE_REGISTRY: dict[str, type[ForgeBackend]] = {
    "github": GitHubBackend,
    "gitlab": GitLabBackend,
}


def build_forge_backend(workflow_cfg: WorkflowConfig) -> ForgeBackend | None:
    """Build a ForgeBackend from the workflow config.

    Returns ``None`` when ``forge.provider == "none"`` (no forge integration).
    When ``provider == "auto"``, parses ``git remote get-url origin`` to
    determine github or gitlab from the remote URL host.
    """
    provider = workflow_cfg.forge.provider

    if provider == "none":
        return None

    if provider == "auto":
        provider = _detect_provider_from_remote()
        if provider == "none":
            logger.warning("forge.provider=auto but could not detect provider from remote")
            return None

    cls = _FORGE_REGISTRY.get(provider)
    if cls is None:
        raise ValueError(
            f"Unknown forge provider '{provider}'. "
            f"Supported: {sorted(_FORGE_REGISTRY)} (+ 'none', 'auto')"
        )

    config = _build_config(provider)
    backend = cls(config)
    logger.info("Built forge backend: %s", backend.name)
    return backend


def _build_config(provider: str) -> dict[str, Any]:
    """Build the config dict for a forge backend from env vars."""
    if provider == "github":
        return {
            "token": os.getenv("GITHUB_TOKEN", ""),
            "repo": os.getenv("GITHUB_REPO", ""),
            "api_url": os.getenv("GITHUB_API_URL", "https://api.github.com"),
        }
    if provider == "gitlab":
        return {
            "token": os.getenv("GITLAB_TOKEN", ""),
            "project_id": os.getenv("GITLAB_PROJECT_ID", ""),
            "api_url": os.getenv("GITLAB_API_URL", "https://gitlab.com/api/v4"),
        }
    return {}


def _detect_provider_from_remote() -> str:
    """Detect forge provider from the git remote origin URL.

    Returns 'github', 'gitlab', or 'none'.
    """
    try:
        from git import Repo

        repo = Repo(os.getcwd())
        remote_url = repo.remotes.origin.url
    except Exception:
        return "none"

    remote_url = remote_url.lower()
    if "github.com" in remote_url:
        return "github"
    if "gitlab.com" in remote_url or "gitlab" in remote_url:
        return "gitlab"
    return "none"


def register_forge_backend(name: str, cls: type[ForgeBackend]) -> None:
    """Register a custom forge backend adapter."""
    if not issubclass(cls, ForgeBackend):
        raise TypeError(f"{cls} must be a subclass of ForgeBackend")
    _FORGE_REGISTRY[name] = cls
