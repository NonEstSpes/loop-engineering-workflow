"""Unit tests for the publish_approval node."""

from __future__ import annotations

from unittest.mock import patch

from devflow.config import AgentConfig, Config, HitlStrategy, WorkflowConfig
from devflow.schemas import Plan, PlanStep
from devflow.state import CheckerReport, CheckerVerdict, Task, WorkflowState


def _make_state(
    hitl_strategy: str = HitlStrategy.FULL_DETAIL,
    human_in_the_loop: bool = True,
) -> WorkflowState:
    """Build a minimal WorkflowState for publish_approval tests."""
    task = Task(id="T-1", title="Test", description="desc")
    plan = Plan(summary="Do thing", steps=[PlanStep(id="s1", description="step")])
    report = CheckerReport(
        agent_name="checker_a",
        verdict=CheckerVerdict.APPROVE,
        summary="ok",
    )
    return WorkflowState(
        task=task,
        plan=plan,
        diff="--- a\n+++ b\n",
        checker_reports=[report],
        self_review_notes="looks good",
        branch_name="devflow/T-1/abc12345",
    )


def _make_config(hitl_strategy: str, human_in_the_loop: bool) -> Config:
    """Build a minimal Config with the given strategy."""
    wf = WorkflowConfig(
        task_source="mock",
        human_in_the_loop=human_in_the_loop,
        hitl_strategy=hitl_strategy,
    )
    agents = {
        "publish_approval": AgentConfig(
            name="publish_approval",
            provider="mock",
            model="mock",
            system_prompt="test",
            auto_approve=False,
        ),
    }
    return Config(workflow=wf, providers={}, agents=agents)


def test_publish_approval_interrupts_in_full_detail() -> None:
    """In full_detail mode, the node calls interrupt() and waits for resume."""
    state = _make_state(hitl_strategy=HitlStrategy.FULL_DETAIL)
    cfg = _make_config(HitlStrategy.FULL_DETAIL, human_in_the_loop=True)

    resume_value = {"approved": True, "reason": "ok", "requested_changes": []}
    with patch("devflow.nodes.publish_approval.interrupt", return_value=resume_value) as mock_int:
        from devflow.nodes.publish_approval import publish_approval_node

        result = publish_approval_node(state, app_cfg=cfg)

    mock_int.assert_called_once()
    payload = mock_int.call_args[0][0]
    assert payload["gate_type"] == "publish_approval"
    assert payload["task_id"] == "T-1"
    assert payload["diff"] == "--- a\n+++ b\n"
    assert len(payload["checker_reports"]) == 1
    assert result["publish_approved"] is True


def test_publish_approval_auto_approves_in_per_plan() -> None:
    """In per_plan mode, publish is auto-approved (no interrupt)."""
    state = _make_state(hitl_strategy=HitlStrategy.PER_PLAN)
    cfg = _make_config(HitlStrategy.PER_PLAN, human_in_the_loop=True)

    with patch("devflow.nodes.publish_approval.interrupt") as mock_int:
        from devflow.nodes.publish_approval import publish_approval_node

        result = publish_approval_node(state, app_cfg=cfg)

    mock_int.assert_not_called()
    assert result["publish_approved"] is True


def test_publish_approval_auto_approves_in_end_of_day() -> None:
    """In end_of_day mode, publish is auto-approved (batch publish happens in Phase 4)."""
    state = _make_state(hitl_strategy=HitlStrategy.END_OF_DAY)
    cfg = _make_config(HitlStrategy.END_OF_DAY, human_in_the_loop=True)

    with patch("devflow.nodes.publish_approval.interrupt") as mock_int:
        from devflow.nodes.publish_approval import publish_approval_node

        result = publish_approval_node(state, app_cfg=cfg)

    mock_int.assert_not_called()
    assert result["publish_approved"] is True


def test_publish_approval_auto_approves_when_human_loop_off() -> None:
    """When human_in_the_loop is off, no interrupt regardless of strategy."""
    state = _make_state(hitl_strategy=HitlStrategy.FULL_DETAIL)
    cfg = _make_config(HitlStrategy.FULL_DETAIL, human_in_the_loop=False)

    with patch("devflow.nodes.publish_approval.interrupt") as mock_int:
        from devflow.nodes.publish_approval import publish_approval_node

        result = publish_approval_node(state, app_cfg=cfg)

    mock_int.assert_not_called()
    assert result["publish_approved"] is True


def test_publish_approval_rejection_propagates() -> None:
    """A rejection via resume value sets publish_approved=False."""
    state = _make_state(hitl_strategy=HitlStrategy.FULL_DETAIL)
    cfg = _make_config(HitlStrategy.FULL_DETAIL, human_in_the_loop=True)

    resume_value = {"approved": False, "reason": "bad diff", "requested_changes": ["fix X"]}
    with patch("devflow.nodes.publish_approval.interrupt", return_value=resume_value):
        from devflow.nodes.publish_approval import publish_approval_node

        result = publish_approval_node(state, app_cfg=cfg)

    assert result["publish_approved"] is False
