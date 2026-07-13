"""Unit tests for the reporter node's notification publishing."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from devflow.config import Config
from devflow.nodes.reporter import reporter_node
from devflow.schemas import Plan, PlanStep, ReporterResponse
from devflow.state import (
    CheckerReport,
    CheckerVerdict,
    FinalVerdict,
    Task,
    WorkflowError,
    WorkflowState,
)
from devflow.todo import CHECKBOX_DONE, TodoItem, parse_todo


@pytest.fixture
def base_state(mock_config: Config, fake_llm_factory: object) -> WorkflowState:
    """A state ready for the reporter to run."""
    return {
        "task": Task(id="T-9", title="Sample", description="desc"),
        "plan": Plan(summary="s", steps=[PlanStep(id="1", description="d")]),
        "diff": "diff content",
        "checker_reports": [
            CheckerReport(agent_name="checker_a", verdict=CheckerVerdict.APPROVE, summary="ok"),
        ],
        "final_verdict": FinalVerdict.APPROVE,
        "branch_name": "devflow/T-9/abc12345",
        "logs": [],
    }


# ---------------------------------------------------------------------------
# happy path with console channel
# ---------------------------------------------------------------------------


def test_reporter_publishes_to_console(
    base_state: WorkflowState,
    mock_config: Config,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_config.workflow.corporate_report_channels = ["console"]
    with caplog.at_level(logging.INFO, logger="devflow.notifications.console"):
        result = reporter_node(base_state, app_cfg=mock_config)

    assert result.get("error") is None
    # report_url is "console" when only console is configured.
    assert result["report_url"] == "console"
    assert "Workflow report" in caplog.text


# ---------------------------------------------------------------------------
# error notification
# ---------------------------------------------------------------------------


def test_reporter_notifies_on_error(
    mock_config: Config,
    fake_llm_factory: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    state: WorkflowState = {
        "task": Task(id="T-1", title="Boom", description="d"),
        "plan": Plan(summary="s", steps=[PlanStep(id="1", description="d")]),
        "error": WorkflowError(node="maker", message="kaboom"),
        "logs": [],
    }
    mock_config.workflow.corporate_report_channels = ["console"]
    with caplog.at_level(20, logger="devflow.notifications.console"):
        reporter_node(state, app_cfg=mock_config)

    # The error markdown is published to the console channel.
    assert "error" in caplog.text.lower()
    assert "kaboom" in caplog.text


# ---------------------------------------------------------------------------
# channel failure does not abort the node
# ---------------------------------------------------------------------------


def test_reporter_survives_channel_failure(
    base_state: WorkflowState,
    mock_config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing notification channel is logged but does not fail the reporter."""
    from devflow.notifications import factory as notif_factory

    class ExplodingChannel:
        name = "console"

        def __init__(self, config: dict[str, Any]) -> None:
            pass

        def send(self, message: str, *, parse_mode: str | None = None) -> str:
            raise RuntimeError("channel down")

        def close(self) -> None:
            pass

    monkeypatch.setitem(
        notif_factory._NOTIFICATION_REGISTRY, "console", ExplodingChannel
    )
    result = reporter_node(base_state, app_cfg=mock_config)

    assert result.get("error") is None
    # report_url is None because the only channel failed.
    assert result.get("report_url") is None


# ---------------------------------------------------------------------------
# TODO.md result recording
# ---------------------------------------------------------------------------


def _write_todo(tmp_path: Path, body: str) -> TodoItem:
    path = tmp_path / "TODO.md"
    path.write_text(body + "\n", encoding="utf-8")
    return parse_todo(path)[0]


def test_writes_done_result_into_todo_line(
    tmp_path: Path, mock_config: Config, fake_llm_factory: object
) -> None:
    mock_config.workflow.todo_path = str(tmp_path / "TODO.md")
    item = _write_todo(tmp_path, "- [~] #r1 [#42](https://t/42) — Fix the bug")
    state: WorkflowState = {
        "task": Task(id="42", title="Fix the bug", description="d"),
        "plan": Plan(summary="s", steps=[PlanStep(id="1", description="d")]),
        "diff": "diff",
        "checker_reports": [],
        "final_verdict": FinalVerdict.APPROVE,
        "todo_item": item,
    }

    result = reporter_node(state, app_cfg=mock_config)

    assert result.get("error") is None
    text = (tmp_path / "TODO.md").read_text(encoding="utf-8")
    assert CHECKBOX_DONE in text
    assert "✅ done:" in text
    assert "Fix the bug" in text


def test_writes_problem_result_for_reject(
    tmp_path: Path, mock_config: Config, fake_llm_factory: object
) -> None:
    mock_config.workflow.todo_path = str(tmp_path / "TODO.md")
    item = _write_todo(tmp_path, "- [~] #r1 — Risky task")
    state: WorkflowState = {
        "task": Task(id="local-1", title="Risky task", description="d"),
        "plan": Plan(summary="s", steps=[PlanStep(id="1", description="d")]),
        "diff": "diff",
        "checker_reports": [],
        "final_verdict": FinalVerdict.REJECT,
        "todo_item": item,
    }

    reporter_node(state, app_cfg=mock_config)

    text = (tmp_path / "TODO.md").read_text(encoding="utf-8")
    assert "⚠️ problem:" in text


def test_skips_todo_when_no_todo_item(
    tmp_path: Path, mock_config: Config, fake_llm_factory: object
) -> None:
    """A run without todo_item (e.g. explicit --task-id) leaves TODO untouched."""
    todo_path = tmp_path / "TODO.md"
    todo_path.write_text("- [ ] #r1 — Untouched\n", encoding="utf-8")
    mock_config.workflow.todo_path = str(todo_path)
    state: WorkflowState = {
        "task": Task(id="MOCK-1", title="t", description="d"),
        "plan": Plan(summary="s", steps=[PlanStep(id="1", description="d")]),
        "diff": "diff",
        "checker_reports": [],
        "final_verdict": FinalVerdict.APPROVE,
        "todo_item": None,
    }

    result = reporter_node(state, app_cfg=mock_config)

    assert result.get("error") is None
    assert todo_path.read_text(encoding="utf-8") == "- [ ] #r1 — Untouched\n"


def test_truncates_long_report(
    tmp_path: Path,
    mock_config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_config.workflow.todo_path = str(tmp_path / "TODO.md")
    item = _write_todo(tmp_path, "- [~] #r1 [#42](https://t/42) — Title")

    from tests.conftest import FakeChatModel

    fake = FakeChatModel(
        outputs={
            ReporterResponse: ReporterResponse(
                pr_title="t",
                pr_description="d",
                corporate_report="x" * 1000,
            )
        }
    )
    import devflow.llm_factory as llm_factory

    monkeypatch.setattr(llm_factory, "_build_llm_impl", lambda cfg, app_cfg: fake)

    state: WorkflowState = {
        "task": Task(id="42", title="Title", description="d"),
        "plan": Plan(summary="s", steps=[PlanStep(id="1", description="d")]),
        "diff": "diff",
        "checker_reports": [],
        "final_verdict": FinalVerdict.APPROVE,
        "todo_item": item,
    }
    reporter_node(state, app_cfg=mock_config)

    text = (tmp_path / "TODO.md").read_text(encoding="utf-8")
    # Full 1000-char blob is not dumped; truncation marker present.
    assert "x" * 1000 not in text
    assert "…" in text


# ---------------------------------------------------------------------------
# config-driven forge actions (Task 6)
# ---------------------------------------------------------------------------


def test_reporter_executes_only_enabled_actions(
    base_state: WorkflowState,
    mock_config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When forge.actions excludes create_mr, no MR is created."""
    mock_config.workflow.forge.actions = ["publish_report", "update_tracker", "record_todo"]

    push_called: list[bool] = []
    mr_called: list[bool] = []

    class FakeForge:
        name = "fake"

        def push(self, branch, target, repo_path):
            push_called.append(True)
            return "sha-fake"

        def create_mr(self, branch, target, title, description):
            mr_called.append(True)
            from devflow.forge.base import MRInfo
            return MRInfo(url="https://fake/mr/1", number=1)

        def healthcheck(self):
            return True

        def close(self):
            pass

    monkeypatch.setattr(
        "devflow.nodes.reporter.build_forge_backend", lambda wf: FakeForge()
    )

    result = reporter_node(base_state, app_cfg=mock_config)

    # push and create_mr were NOT called (not in actions list)
    assert push_called == []
    assert mr_called == []
    assert result.get("mr_url") is None


def test_reporter_creates_mr_when_action_enabled(
    base_state: WorkflowState,
    mock_config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When create_mr is in actions, the reporter creates an MR via forge."""
    mock_config.workflow.forge.actions = ["create_mr"]
    mock_config.workflow.forge.provider = "github"

    class FakeForge:
        name = "fake"

        def push(self, branch, target, repo_path):
            return "sha-fake"

        def create_mr(self, branch, target, title, description):
            from devflow.forge.base import MRInfo
            return MRInfo(url="https://github.com/owner/repo/pull/1", number=1)

        def healthcheck(self):
            return True

        def close(self):
            pass

    monkeypatch.setattr(
        "devflow.nodes.reporter.build_forge_backend", lambda wf: FakeForge()
    )

    result = reporter_node(base_state, app_cfg=mock_config)

    assert result.get("mr_url") == "https://github.com/owner/repo/pull/1"
    # backward compat: pr_url mirrors mr_url when create_mr runs
    assert result.get("pr_url") == "https://github.com/owner/repo/pull/1"


def test_reporter_pushes_when_action_enabled(
    base_state: WorkflowState,
    mock_config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When push is in actions, the reporter pushes the branch."""
    mock_config.workflow.forge.actions = ["push"]
    mock_config.workflow.forge.provider = "github"

    pushed: list[str] = []

    class FakeForge:
        name = "fake"

        def push(self, branch, target, repo_path):
            pushed.append(branch)
            return "sha-pushed"

        def create_mr(self, branch, target, title, description):
            from devflow.forge.base import MRInfo
            return MRInfo(url="https://fake/mr/1", number=1)

        def healthcheck(self):
            return True

        def close(self):
            pass

    monkeypatch.setattr(
        "devflow.nodes.reporter.build_forge_backend", lambda wf: FakeForge()
    )

    result = reporter_node(base_state, app_cfg=mock_config)

    assert len(pushed) == 1
    assert result.get("pushed_sha") == "sha-pushed"


# ---------------------------------------------------------------------------
# prepare-only mode (Task 3: end_of_day per-task)
# ---------------------------------------------------------------------------


def test_reporter_prepare_only_skips_publish_and_push(
    base_state: WorkflowState,
    mock_config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """prepare_only=True runs LLM + record_todo but skips publish/push/MR."""
    mock_config.workflow.forge.provider = "github"
    mock_config.workflow.forge.actions = [
        "publish_report",
        "update_tracker",
        "record_todo",
        "push",
        "create_mr",
    ]

    push_called: list[bool] = []
    mr_called: list[bool] = []
    publish_called: list[bool] = []

    class FakeForge:
        name = "fake"

        def push(self, branch, target, repo_path):
            push_called.append(True)
            return "sha-fake"

        def create_mr(self, branch, target, title, description):
            mr_called.append(True)
            from devflow.forge.base import MRInfo
            return MRInfo(url="https://fake/mr/1", number=1)

        def healthcheck(self):
            return True

        def close(self):
            pass

    monkeypatch.setattr(
        "devflow.nodes.reporter.build_forge_backend", lambda wf: FakeForge()
    )
    monkeypatch.setattr(
        "devflow.nodes.reporter._publish_to_channels",
        lambda cfg, msg: publish_called.append(True) or "console",
    )

    result = reporter_node(base_state, app_cfg=mock_config, prepare_only=True)

    # Artifacts ARE generated.
    artifacts = result.get("reporter_artifacts")
    assert artifacts is not None
    assert hasattr(artifacts, "pr_title")
    # publish/push/MR are NOT executed.
    assert push_called == []
    assert mr_called == []
    assert publish_called == []
    assert result.get("pushed_sha") is None
    assert result.get("mr_url") is None
    assert result.get("report_url") is None


def test_reporter_prepare_only_returns_artifacts(
    base_state: WorkflowState,
    mock_config: Config,
) -> None:
    """prepare_only=True returns the ReporterResponse in reporter_artifacts."""
    result = reporter_node(base_state, app_cfg=mock_config, prepare_only=True)
    artifacts = result.get("reporter_artifacts")
    assert artifacts is not None
    from devflow.schemas import ReporterResponse
    assert isinstance(artifacts, ReporterResponse)
    assert artifacts.pr_title  # non-empty


def test_reporter_default_is_not_prepare_only(
    base_state: WorkflowState,
    mock_config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without prepare_only, publish_report still runs (default behavior)."""
    publish_called: list[bool] = []
    monkeypatch.setattr(
        "devflow.nodes.reporter._publish_to_channels",
        lambda cfg, msg: publish_called.append(True) or "console",
    )

    result = reporter_node(base_state, app_cfg=mock_config)
    assert publish_called == [True]
    assert result.get("report_url") == "console"
