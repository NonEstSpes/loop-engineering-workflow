"""Integration test: forge push + MR through the reporter node.

Verifies that when forge.actions includes push + create_mr, the reporter
calls the forge backend to push the branch and create an MR.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from git import Repo

from devflow.config import Config
from devflow.forge.base import MRInfo
from devflow.nodes.reporter import reporter_node
from devflow.schemas import Plan, PlanStep
from devflow.state import CheckerReport, CheckerVerdict, FinalVerdict, Task, WorkflowState


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


def _make_state(repo_path: str) -> WorkflowState:
    task = Task(id="T-1", title="Test task", description="A test task")
    plan = Plan(summary="Do thing", steps=[PlanStep(id="s1", description="step")])
    report = CheckerReport(
        agent_name="checker_a",
        verdict=CheckerVerdict.APPROVE,
        summary="ok",
    )
    return WorkflowState(
        task=task,
        plan=plan,
        diff="--- a\n+++ b\n+hello\n",
        checker_reports=[report],
        final_verdict=FinalVerdict.APPROVE,
        branch_name="devflow/T-1/abc12345",
        worktree_path=repo_path,
    )


class RecordingForge:
    """A fake forge backend that records push and create_mr calls."""

    name = "recording"

    def __init__(self) -> None:
        self.pushed: list[tuple[str, str, str]] = []
        self.mrs_created: list[dict[str, str]] = []

    def push(self, branch: str, target: str, repo_path: str) -> str:
        self.pushed.append((branch, target, repo_path))
        return "sha-recorded"

    def create_mr(self, branch: str, target: str, title: str, description: str) -> MRInfo:
        self.mrs_created.append({
            "branch": branch,
            "target": target,
            "title": title,
            "description": description,
        })
        return MRInfo(url="https://example.com/mr/1", number=1)

    def healthcheck(self) -> bool:
        return True

    def close(self) -> None:
        pass


def test_reporter_pushes_and_creates_mr(
    temp_git_repo: Path,
    mock_config: Config,
    fake_llm_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reporter pushes branch and creates MR when both actions are enabled."""
    state = _make_state(str(temp_git_repo))

    mock_config.workflow.forge.provider = "github"
    mock_config.workflow.forge.actions = ["push", "create_mr"]

    recording_forge = RecordingForge()
    monkeypatch.setattr(
        "devflow.nodes.reporter.build_forge_backend", lambda wf: recording_forge
    )

    result = reporter_node(state, app_cfg=mock_config)

    # push was called with the branch name
    assert len(recording_forge.pushed) == 1
    assert recording_forge.pushed[0][0] == "devflow/T-1/abc12345"

    # create_mr was called
    assert len(recording_forge.mrs_created) == 1
    assert recording_forge.mrs_created[0]["branch"] == "devflow/T-1/abc12345"

    # Results propagated to state
    assert result.get("pushed_sha") == "sha-recorded"
    assert result.get("mr_url") == "https://example.com/mr/1"


def test_reporter_skips_forge_when_actions_excluded(
    temp_git_repo: Path,
    mock_config: Config,
    fake_llm_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reporter does not push or create MR when actions are excluded."""
    state = _make_state(str(temp_git_repo))

    mock_config.workflow.forge.provider = "github"
    mock_config.workflow.forge.actions = ["publish_report"]  # no push, no create_mr

    recording_forge = RecordingForge()
    monkeypatch.setattr(
        "devflow.nodes.reporter.build_forge_backend", lambda wf: recording_forge
    )

    result = reporter_node(state, app_cfg=mock_config)

    assert recording_forge.pushed == []
    assert recording_forge.mrs_created == []
    assert result.get("pushed_sha") is None
    assert result.get("mr_url") is None
