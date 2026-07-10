"""Tests for the DevFlow CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from devflow.cli import app
from devflow.state import Task, WorkflowState

runner = CliRunner()


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Create a minimal valid configuration directory for CLI tests."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    agents = [
        "orchestrator",
        "planner",
        "plan_approval",
        "maker",
        "self_review",
        "checker_a",
        "checker_b",
        "checker_c",
        "reporter",
        "research",
    ]
    for name in agents:
        (agents_dir / f"{name}.md").write_text(
            f"---\nname: {name}\nprovider: mock\nmodel: mock-model\n---\n\n"
            f"You are the {name} agent.\n",
            encoding="utf-8",
        )

    (tmp_path / "workflow.yaml").write_text(
        "task_source: mock\n"
        "max_rework_iterations: 3\n"
        "human_in_the_loop: false\n"
        "default_branch: main\n"
        "pr_target_branch: main\n"
        "corporate_report_channels:\n  - console\n",
        encoding="utf-8",
    )
    (tmp_path / "providers.yaml").write_text(
        "providers:\n"
        "  mock:\n"
        "    type: mock\n",
        encoding="utf-8",
    )
    return tmp_path


def test_list_tasks_table(config_dir: Path) -> None:
    result = runner.invoke(app, ["--config-dir", str(config_dir), "list-tasks"])
    assert result.exit_code == 0
    assert "MOCK-1" in result.output
    assert "MOCK-2" in result.output
    assert "not started" in result.output


def test_list_tasks_json(config_dir: Path) -> None:
    result = runner.invoke(
        app, ["--config-dir", str(config_dir), "list-tasks", "--format", "json"]
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 2
    assert data[0]["id"] == "MOCK-1"
    assert data[0]["status"] == "open"
    assert data[0]["progress"] == "not started"
    assert data[0]["problems"] == "-"


def test_list_tasks_limit(config_dir: Path) -> None:
    result = runner.invoke(
        app, ["--config-dir", str(config_dir), "list-tasks", "--limit", "1"]
    )
    assert result.exit_code == 0
    assert "MOCK-1" in result.output
    assert "MOCK-2" not in result.output


def test_list_tasks_status_filter_no_matches(config_dir: Path) -> None:
    result = runner.invoke(
        app, ["--config-dir", str(config_dir), "list-tasks", "--status", "closed"]
    )
    assert result.exit_code == 0
    assert "No tasks found" in result.output


def test_list_tasks_start_task_id_runs_workflow(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    def _fake_run_workflow(*, task_id: str | None = None, **kwargs: Any) -> WorkflowState:
        calls.append({"task_id": task_id, "kwargs": kwargs})
        return {
            "task": Task(id=task_id or "", title="", description=""),
            "final_verdict": None,
            "error": None,
        }

    monkeypatch.setattr("devflow.cli.run_workflow", _fake_run_workflow)

    result = runner.invoke(
        app,
        [
            "--config-dir",
            str(config_dir),
            "list-tasks",
            "--start-task-id",
            "MOCK-1",
        ],
    )
    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0]["task_id"] == "MOCK-1"
    assert calls[0]["kwargs"].get("thread_id") == "MOCK-1"
    assert "Starting task MOCK-1" in result.output


def test_config_error_exits_with_code_2(tmp_path: Path) -> None:
    bad_config_dir = tmp_path / "missing"
    result = runner.invoke(app, ["--config-dir", str(bad_config_dir), "list-tasks"])
    assert result.exit_code == 2
    assert "Configuration error" in result.output
