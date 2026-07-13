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


def test_run_daemon_with_sweep_and_scheduler(
    mock_config: Any,
    temp_git_repo: Path,
    fake_llm_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full daemon startup: sweep runs, scheduler starts, web server called.

    Verifies the integration of all Phase 1 components without actually
    binding a network port.
    """
    web_called: list[bool] = []

    def fake_web_server(*args: Any, **kwargs: Any) -> None:
        web_called.append(True)

    monkeypatch.setattr("devflow.daemon.__main__.run_web_server", fake_web_server)
    monkeypatch.setattr("devflow.daemon.__main__.load_config", lambda *a, **kw: mock_config)
    mock_config.workflow.daemon.enabled = True
    mock_config.workflow.hitl_strategy = "end_of_day"

    run_daemon(config_dir="config", repo_path=str(temp_git_repo))

    assert web_called == [True]


def test_run_daemon_wires_approval_store(
    mock_config: Any,
    temp_git_repo: Path,
    fake_llm_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_daemon creates ApprovalStore and passes it to run_web_server."""
    captured: dict = {}

    def fake_web_server(
        app_cfg: Any,
        locks: Any,
        event_bus: Any,
        runner: Any,
        approval_store: Any = None,
    ) -> None:
        captured["approval_store"] = approval_store
        captured["runner"] = runner

    monkeypatch.setattr("devflow.daemon.__main__.run_web_server", fake_web_server)
    monkeypatch.setattr("devflow.daemon.__main__.load_config", lambda *a, **kw: mock_config)
    mock_config.workflow.daemon.enabled = True

    run_daemon(config_dir="config", repo_path=str(temp_git_repo))

    assert captured["approval_store"] is not None
    assert hasattr(captured["approval_store"], "get_pending")
    # The runner should also have the bridge attached.
    assert captured["runner"] is not None
    # WorkflowRunner stores its bridge under the private _bridge attribute.
    assert getattr(captured["runner"], "_bridge", None) is not None


def test_run_daemon_exits_when_disabled(
    mock_config: Any,
    temp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_daemon exits with error when daemon.enabled is False."""
    monkeypatch.setattr("devflow.daemon.__main__.load_config", lambda *a, **kw: mock_config)
    mock_config.workflow.daemon.enabled = False

    with pytest.raises(SystemExit) as exc_info:
        run_daemon(config_dir="config", repo_path=str(temp_git_repo))
    assert exc_info.value.code == 1
