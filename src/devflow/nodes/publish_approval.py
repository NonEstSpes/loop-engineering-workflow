"""Publish approval node: the post-implementation human-in-the-loop gate.

This is the SECOND gate (after plan_approval). It sits between
aggregate_checker and reporter. In ``full_detail`` strategy it pauses the
workflow via ``interrupt()`` so the human can review the diff, checker
reports, and self-review before publication. In ``per_plan`` and
``end_of_day`` it auto-approves (no pause).
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.types import interrupt

from devflow.config import Config, HitlStrategy
from devflow.state import WorkflowError, WorkflowState

logger = logging.getLogger(__name__)


def publish_approval_node(
    state: WorkflowState,
    *,
    app_cfg: Config,
) -> dict[str, Any]:
    """Gate before the reporter: approve or reject the finished work.

    Strategy behaviour:
    - ``per_plan``: auto-approve (the plan was already approved).
    - ``full_detail``: interrupt and ask the human to review diff + checkers.
    - ``end_of_day``: auto-approve (batch publish is handled in Phase 4).

    When ``human_in_the_loop`` is off, auto-approve regardless of strategy.
    """
    task = state.get("task")
    if task is None:
        return {
            "error": WorkflowError(
                node="publish_approval",
                message="Missing task in state",
            ),
            "logs": ["publish_approval: error - missing task"],
        }

    strategy = app_cfg.workflow.hitl_strategy
    hil = app_cfg.workflow.human_in_the_loop

    # Auto-approve unless we are in full_detail mode with HITL on.
    should_interrupt = hil and strategy == HitlStrategy.FULL_DETAIL

    if not should_interrupt:
        logger.info(
            "publish_approval: auto-approve (strategy=%s, hitl=%s)", strategy, hil
        )
        return {"publish_approved": True, "logs": ["publish_approval: auto-approved"]}

    # Build the interrupt payload for the human to review.
    plan = state.get("plan")
    diff = state.get("diff") or ""
    reports = state.get("checker_reports", [])
    self_review_notes = state.get("self_review_notes")
    branch = state.get("branch_name")

    payload: dict[str, Any] = {
        "gate_type": "publish_approval",
        "task_id": task.id,
        "task_title": task.title,
        "plan_summary": plan.summary if plan else "",
        "plan_steps": (
            [{"id": s.id, "description": s.description} for s in plan.steps]
            if plan
            else []
        ),
        "diff": diff,
        "checker_reports": [
            {
                "agent_name": r.agent_name,
                "verdict": r.verdict.value,
                "summary": r.summary,
                "findings": r.findings,
            }
            for r in reports
        ],
        "self_review_notes": self_review_notes or "",
        "branch": branch,
    }

    logger.info("publish_approval: interrupting for human review (task %s)", task.id)

    resume_value = interrupt(payload)
    approved = bool(resume_value.get("approved"))
    reason = str(resume_value.get("reason", ""))
    requested_changes = list(resume_value.get("requested_changes", []))

    logger.info(
        "publish_approval: decision=%s, reason=%s, changes=%d",
        approved,
        reason,
        len(requested_changes),
    )

    return {
        "publish_approved": approved,
        "logs": [
            f"publish_approval: {'approved' if approved else 'rejected'}"
            + (f" — {reason}" if reason else ""),
        ],
    }
