"""Planner node: create an implementation plan for the current task."""

from __future__ import annotations

import logging
from typing import Any

from devflow.config import Config
from devflow.llm_factory import build_llm
from devflow.state import Plan, WorkflowError, WorkflowState
from devflow.tools.code_tools import CodeTools
from devflow.utils.structured_llm import call_structured

logger = logging.getLogger(__name__)


def planner_node(
    state: WorkflowState,
    *,
    app_cfg: Config,
    repo_path: str | None = None,
) -> dict[str, Any]:
    """Generate an implementation plan for the current task."""
    task = state.get("task")
    if task is None:
        return {
            "error": WorkflowError(node="planner", message="No task in state"),
            "logs": ["planner: error - no task in state"],
        }

    agent_cfg = app_cfg.agents.get("planner")
    if agent_cfg is None:
        return {
            "error": WorkflowError(node="planner", message="Planner agent config not found"),
            "logs": ["planner: error - planner agent config not found"],
        }

    try:
        llm = build_llm(agent_cfg, app_cfg)
        repo_context = _build_repo_context(repo_path)

        user_prompt = f"""Task ID: {task.id}
Title: {task.title}
Description:
{task.description}

Repository context:
{repo_context}

Produce an implementation plan following the configured format.
"""

        plan = call_structured(llm, agent_cfg.system_prompt, user_prompt, Plan)
        logger.info("Planner produced plan with %d steps", len(plan.steps))
        return {
            "plan": plan,
            "logs": [f"planner: produced plan with {len(plan.steps)} steps"],
        }
    except Exception as exc:
        logger.exception("Planner failed")
        return {
            "error": WorkflowError(node="planner", message=str(exc)),
            "logs": [f"planner: error {exc}"],
        }


def _build_repo_context(repo_path: str | None) -> str:
    """Build a lightweight summary of the repository for the planner."""
    if repo_path is None:
        return "No repository path provided. Plan based on task description only."
    try:
        tools = CodeTools(repo_path)
        files = tools.list_tree()[:50]
        return "Top-level files:\n" + "\n".join(files)
    except Exception as exc:
        return f"Could not read repository: {exc}"
