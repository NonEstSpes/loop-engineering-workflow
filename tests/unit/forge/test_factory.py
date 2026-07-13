"""Unit tests for the forge factory."""

from __future__ import annotations

import pytest

from devflow.config import ForgeConfig, WorkflowConfig
from devflow.forge.base import ForgeBackend
from devflow.forge.factory import build_forge_backend


def _make_workflow(provider: str = "none") -> WorkflowConfig:
    wf = WorkflowConfig(task_source="mock")
    wf.forge = ForgeConfig(provider=provider)
    return wf


def test_build_forge_returns_none_for_none_provider() -> None:
    """build_forge_backend returns None when provider is 'none'."""
    wf = _make_workflow("none")
    assert build_forge_backend(wf) is None


def test_build_forge_github() -> None:
    """build_forge_backend returns GitHubBackend when provider is 'github'."""
    wf = _make_workflow("github")
    backend = build_forge_backend(wf)
    assert backend is not None
    assert backend.name == "github"


def test_build_forge_gitlab() -> None:
    """build_forge_backend returns GitLabBackend when provider is 'gitlab'."""
    wf = _make_workflow("gitlab")
    backend = build_forge_backend(wf)
    assert backend is not None
    assert backend.name == "gitlab"


def test_build_forge_unknown_provider_raises() -> None:
    """build_forge_backend raises ValueError for an unknown provider."""
    wf = _make_workflow("bogus")
    with pytest.raises(ValueError, match="Unknown forge provider"):
        build_forge_backend(wf)


def test_register_custom_forge_backend() -> None:
    """register_forge_backend adds a new provider to the registry."""
    from devflow.forge.base import MRInfo
    from devflow.forge.factory import register_forge_backend

    class CustomBackend(ForgeBackend):
        name = "custom"

        def push(self, branch: str, target: str, repo_path: str) -> str:
            return "sha"

        def create_mr(self, branch: str, target: str, title: str, description: str) -> MRInfo:
            return MRInfo(url="https://custom/mr/1")

    register_forge_backend("custom", CustomBackend)
    wf = _make_workflow("custom")
    backend = build_forge_backend(wf)
    assert backend is not None
    assert backend.name == "custom"
