"""Tests for the git_tools research driver."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from devflow.research.schemas import ResearchRequest
from devflow.research.sources.git_tools import GitToolsSource


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository with a couple of commits."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    (repo / "hello.py").write_text("def hello() -> str:\n    return 'hello world'\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial hello world"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    (repo / "hello.py").write_text("def hello() -> str:\n    return 'hello universe'\n")
    subprocess.run(
        ["git", "commit", "-am", "Update greeting to universe"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    return repo


def test_git_tools_healthcheck(git_repo: Path) -> None:
    source = GitToolsSource({"repo_path": str(git_repo)})
    assert source.healthcheck() is True


def test_git_tools_grep_finding(git_repo: Path) -> None:
    source = GitToolsSource({"repo_path": str(git_repo)})
    findings = source.search(ResearchRequest(query="hello universe"))
    grep_findings = [f for f in findings if f.metadata.get("kind") == "grep"]
    assert len(grep_findings) >= 1
    assert grep_findings[0].source == "git_tools"
    assert "hello.py" in grep_findings[0].metadata.get("path", "")


def test_git_tools_log_finding(git_repo: Path) -> None:
    source = GitToolsSource({"repo_path": str(git_repo)})
    findings = source.search(ResearchRequest(query="universe"))
    log_findings = [f for f in findings if f.metadata.get("kind") == "log"]
    assert len(log_findings) >= 1
    assert "universe" in log_findings[0].content


def test_git_tools_empty_query(git_repo: Path) -> None:
    source = GitToolsSource({"repo_path": str(git_repo)})
    findings = source.search(ResearchRequest(query="xyzzy_not_found"))
    assert findings == []
