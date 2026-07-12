"""Test that plan_approval respects hitl_strategy."""

from __future__ import annotations

from unittest.mock import patch

from devflow.config import AgentConfig, Config, HitlStrategy, WorkflowConfig
from devflow.schemas import Plan, PlanStep
from devflow.state import Task, WorkflowState


def _make_config(strategy: str) -> Config:
    wf = WorkflowConfig(
        task_source="mock",
        human_in_the_loop=True,
        hitl_strategy=strategy,
    )
    agents = {
        "plan_approval": AgentConfig(
            name="plan_approval",
            provider="mock",
            model="mock",
            system_prompt="test",
            auto_approve=False,
        ),
    }
    return Config(workflow=wf, providers={}, agents=agents)


def _make_state() -> WorkflowState:
    task = Task(id="T-1", title="Test", description="desc")
    plan = Plan(summary="Do thing", steps=[PlanStep(id="s1", description="step")])
    return WorkflowState(task=task, plan=plan)


def test_plan_approval_interrupts_in_per_plan() -> None:
    """In per_plan mode, the plan is interrupted for human approval."""
    state = _make_state()
    cfg = _make_config(HitlStrategy.PER_PLAN)

    resume = {"approved": True, "reason": "ok", "requested_changes": []}
    with patch("devflow.nodes.plan_approval.interrupt", return_value=resume) as mock_int:
        from devflow.nodes.plan_approval import plan_approval_node

        result = plan_approval_node(state, app_cfg=cfg)

    mock_int.assert_called_once()
    assert result["plan_approved"] is True


def test_plan_approval_auto_approves_in_full_detail() -> None:
    """In full_detail mode, the plan is auto-approved (review happens at publish gate)."""
    state = _make_state()
    cfg = _make_config(HitlStrategy.FULL_DETAIL)

    with patch("devflow.nodes.plan_approval.interrupt") as mock_int:
        from devflow.nodes.plan_approval import plan_approval_node

        result = plan_approval_node(state, app_cfg=cfg)

    mock_int.assert_not_called()
    assert result["plan_approved"] is True


def test_plan_approval_auto_approves_in_end_of_day() -> None:
    """In end_of_day mode, the plan is auto-approved."""
    state = _make_state()
    cfg = _make_config(HitlStrategy.END_OF_DAY)

    with patch("devflow.nodes.plan_approval.interrupt") as mock_int:
        from devflow.nodes.plan_approval import plan_approval_node

        result = plan_approval_node(state, app_cfg=cfg)

    mock_int.assert_not_called()
    assert result["plan_approved"] is True
