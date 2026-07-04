"""Plan approval node: approve, reject, or request changes to a plan."""

from __future__ import annotations

import logging
from typing import Any

from langgraph.types import interrupt

from devflow.config import Config
from devflow.state import WorkflowError, WorkflowState

logger = logging.getLogger(__name__)


def plan_approval_node(state: WorkflowState, *, app_cfg: Config) -> dict[str, Any]:
    """Approve or reject the plan. Supports human-in-the-loop or auto-approval."""
    task = state.get("task")
    plan = state.get("plan")
    if task is None or plan is None:
        return {
            "error": WorkflowError(
                node="plan_approval",
                message="Missing task or plan in state",
            ),
            "logs": ["plan_approval: error - missing task or plan"],
        }

    agent_cfg = app_cfg.agents.get("plan_approval")
    if agent_cfg is None:
        return {
            "error": WorkflowError(
                node="plan_approval",
                message="plan_approval agent config not found",
            ),
            "logs": ["plan_approval: error - agent config not found"],
        }

    # Auto-approval when configured
    if not app_cfg.workflow.human_in_the_loop or agent_cfg.auto_approve:
        logger.info("Auto-approved plan for task %s", task.id)
        return {
            "plan_approved": True,
            "logs": ["plan_approval: auto-approved"],
        }

    # Human-in-the-loop: pause the graph and wait for human input.
    # The interrupt itself must propagate (do not catch it), so only the resume
    # value processing is guarded.
    resume_value = interrupt(
        {
            "task_id": task.id,
            "task_title": task.title,
            "task_description": task.description,
            "plan_summary": plan.summary,
            "plan_steps": [{"id": step.id, "description": step.description} for step in plan.steps],
        }
    )

    try:
        if not isinstance(resume_value, dict):
            raise ValueError(f"Expected dict resume value, got {type(resume_value).__name__}")
        approved = bool(resume_value.get("approved"))
        reason = str(resume_value.get("reason", ""))
        requested_changes = list(resume_value.get("requested_changes", []))
        logger.info(
            "Plan approval human decision for task %s: %s (%d changes requested)",
            task.id,
            approved,
            len(requested_changes),
        )
        return {
            "plan_approved": approved,
            "logs": [
                f"plan_approval: human decision {approved} - {reason}",
                f"plan_approval: requested changes {requested_changes}",
            ],
        }
    except Exception as exc:
        logger.exception("Plan approval failed to process resume value")
        return {
            "error": WorkflowError(node="plan_approval", message=str(exc)),
            "logs": [f"plan_approval: error processing human input: {exc}"],
        }
