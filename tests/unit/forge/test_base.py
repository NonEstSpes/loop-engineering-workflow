"""Unit tests for the ForgeBackend abstract base class."""

from __future__ import annotations

import pytest

from devflow.forge.base import ForgeBackend, MRInfo


def test_mrinfo_model() -> None:
    """MRInfo holds url and optional number."""
    info = MRInfo(url="https://github.com/owner/repo/pull/42", number=42)
    assert info.url == "https://github.com/owner/repo/pull/42"
    assert info.number == 42

    info_no_num = MRInfo(url="https://gitlab.com/owner/repo/-/merge_requests/1")
    assert info_no_num.number is None


def test_forge_backend_is_abstract() -> None:
    """ForgeBackend cannot be instantiated directly."""
    with pytest.raises(TypeError):
        ForgeBackend({})  # type: ignore[abstract]


def test_forge_backend_default_healthcheck() -> None:
    """A concrete subclass gets the default healthcheck (returns True)."""

    class DummyBackend(ForgeBackend):
        name = "dummy"

        def push(self, branch: str, target: str, repo_path: str) -> str:
            return "sha-dummy"

        def create_mr(self, branch: str, target: str, title: str, description: str) -> MRInfo:
            return MRInfo(url="https://example.com/mr/1", number=1)

    backend = DummyBackend({})
    assert backend.healthcheck() is True
    # close() is a no-op by default
    backend.close()


def test_forge_backend_config_stored() -> None:
    """The config dict is accessible on the backend instance."""

    class DummyBackend(ForgeBackend):
        name = "dummy"

        def push(self, branch: str, target: str, repo_path: str) -> str:
            return "sha"

        def create_mr(self, branch: str, target: str, title: str, description: str) -> MRInfo:
            return MRInfo(url="https://example.com/mr/1")

    backend = DummyBackend({"token": "abc", "repo": "owner/repo"})
    assert backend.config["token"] == "abc"
    assert backend.config["repo"] == "owner/repo"
