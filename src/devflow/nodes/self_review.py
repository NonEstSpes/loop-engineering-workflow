"""Self-review node: review the diff produced by the maker."""

from __future__ import annotations

import logging
from typing import Any

from langgraph.types import Command

from devflow.config import Config
from devflow.llm_factory import build_llm
from devflow.nodes.research import (
    format_research_context,
    request_research_command,
    research_budget_exceeded,
    take_research_result,
)
from devflow.schemas import SelfReviewResponse
from devflow.state import WorkflowError, WorkflowState
from devflow.utils.structured_llm import call_structured

logger = logging.getLogger(__name__)


def self_review_node(state: WorkflowState, *, app_cfg: Config) -> dict[str, Any] | Command:
    """Run a self-review over the current diff."""
    task = state.get("task")
    plan = state.get("plan")
    diff = state.get("diff")
    if task is None or diff is None:
        return {
            "error": WorkflowError(
                node="self_review",
                message="Missing task or diff in state",
            ),
            "logs": ["self_review: error - missing task or diff"],
        }

    agent_cfg = app_cfg.agents.get("self_review")
    if agent_cfg is None:
        return {
            "error": WorkflowError(
                node="self_review",
                message="self_review agent config not found",
            ),
            "logs": ["self_review: error - agent config not found"],
        }

    research_result = take_research_result(state, "self_review")

    try:
        llm = build_llm(agent_cfg, app_cfg)
        user_prompt = f"""Task ID: {task.id}
Title: {task.title}
Description:
{task.description}

Plan:
{plan.summary if plan else "No plan available"}

{format_research_context(research_result)}

Diff:
```diff
{diff}
```

If you need additional research before reviewing, set `research_request.query`.
"""
        response = call_structured(
            llm,
            agent_cfg.system_prompt,
            user_prompt,
            SelfReviewResponse,
        )

        if response.research_request is not None and not research_budget_exceeded(state, app_cfg):
            response.research_request.caller = "self_review"
            response.research_request.context = user_prompt
            logger.info("Self-review requested research: %s", response.research_request.query)
            return request_research_command(response.research_request)

        logger.info("Self-review needs_rework: %s", response.needs_rework)
        return {
            "self_review_notes": response.summary,
            "last_research_result": None,
            "logs": [f"self_review: {len(response.issues)} issues, rework={response.needs_rework}"],
        }
    except Exception as exc:
        logger.exception("Self-review failed")
        return {
            "error": WorkflowError(node="self_review", message=str(exc)),
            "logs": [f"self_review: error {exc}"],
        }
