"""Unit tests for the reporter node's notification publishing."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from devflow.config import Config
from devflow.nodes.reporter import reporter_node
from devflow.schemas import Plan, PlanStep
from devflow.state import (
    CheckerReport,
    CheckerVerdict,
    FinalVerdict,
    Task,
    WorkflowError,
    WorkflowState,
)


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
