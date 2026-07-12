"""Unit tests for the daemon entry point."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from git import Repo

from devflow.daemon.__main__ import run_daemon


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
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
    return repo_path


def test_run_daemon_starts_components(
    mock_config: Any,
    temp_git_repo: Path,
    fake_llm_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_daemon initializes scheduler, web app, and runner without crashing.

    We patch run_web_server to avoid actually binding a port / blocking.
    """
    started: list[str] = []

    def fake_web_server(*args: Any, **kwargs: Any) -> None:
        started.append("web")

    monkeypatch.setattr("devflow.daemon.__main__.run_web_server", fake_web_server)

    # Patch load_config to return our mock config.
    monkeypatch.setattr("devflow.daemon.__main__.load_config", lambda *a, **kw: mock_config)
    # Enable daemon mode for the test.
    mock_config.workflow.daemon.enabled = True

    run_daemon(config_dir="config", repo_path=str(temp_git_repo))

    assert "web" in started
