"""Maker node: implement the approved plan in an isolated git worktree."""

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
from devflow.schemas import FileOperation, MakerResponse
from devflow.state import Plan, Task, WorkflowError, WorkflowState
from devflow.tools.code_tools import CodeTools
from devflow.tools.git_worktree import GitWorktreeManager
from devflow.utils.structured_llm import call_structured

logger = logging.getLogger(__name__)


def maker_node(
    state: WorkflowState,
    *,
    app_cfg: Config,
    repo_path: str,
    base_branch: str = "main",
    git_user: tuple[str, str] = ("DevFlow", "devflow@local"),
) -> dict[str, Any] | Command:
    """Implement the plan in a fresh git worktree."""
    task = state.get("task")
    plan = state.get("plan")
    if task is None or plan is None:
        return {
            "error": WorkflowError(node="maker", message="Missing task or plan in state"),
            "logs": ["maker: error - missing task or plan"],
        }

    agent_cfg = app_cfg.agents.get("maker")
    if agent_cfg is None:
        return {
            "error": WorkflowError(node="maker", message="maker agent config not found"),
            "logs": ["maker: error - maker agent config not found"],
        }

    research_result = take_research_result(state, "maker")

    try:
        # Build the prompt and ask the LLM for operations before touching the repo.
        code_tools = CodeTools(repo_path)
        file_tree = code_tools.list_tree()[:100]

        llm = build_llm(agent_cfg, app_cfg)
        user_prompt = _build_maker_prompt(task, plan, file_tree, research_result)
        response = call_structured(llm, agent_cfg.system_prompt, user_prompt, MakerResponse)

        if response.research_request is not None and not research_budget_exceeded(state, app_cfg):
            response.research_request.caller = "maker"
            response.research_request.context = user_prompt
            logger.info("Maker requested research: %s", response.research_request.query)
            return request_research_command(response.research_request)

        # Now that we know what to change, create the worktree and apply operations.
        manager = GitWorktreeManager(repo_path, base_branch=base_branch)
        worktree_path = manager.create(task.id)
        manager.configure_user(*git_user)

        worktree_tools = CodeTools(worktree_path)
        _apply_operations(worktree_tools, response.operations)

        test_output = ""
        for cmd in response.test_commands:
            try:
                test_output += worktree_tools.run_command(cmd) + "\n"
            except Exception as exc:
                test_output += f"Command {' '.join(cmd)} failed: {exc}\n"

        manager.add_and_commit(f"devflow({task.id}): {response.summary}")
        diff = manager.get_diff()

        logger.info("Maker finished for task %s, diff length %d", task.id, len(diff))
        return {
            "worktree_path": str(worktree_path),
            "branch_name": manager.branch_name,
            "diff": diff,
            "last_research_result": None,
            "logs": [
                f"maker: implemented {len(response.operations)} operations",
                f"maker: test output\n{test_output}",
            ],
        }
    except Exception as exc:
        logger.exception("Maker failed")
        return {
            "error": WorkflowError(node="maker", message=str(exc)),
            "logs": [f"maker: error {exc}"],
        }


def _build_maker_prompt(
    task: Task,
    plan: Plan,
    file_tree: list[str],
    research_result: Any | None,
) -> str:
    prompt = f"""Task ID: {task.id}
Title: {task.title}
Description:
{task.description}

Plan:
{plan.summary}

Steps:
"""
    for step in plan.steps:
        prompt += f"- {step.id}: {step.description}\n"
        if step.files_to_touch:
            prompt += f"  Files: {', '.join(step.files_to_touch)}\n"
        if step.tests_to_add:
            prompt += f"  Tests: {', '.join(step.tests_to_add)}\n"

    prompt += "\nRepository files:\n" + "\n".join(file_tree)
    prompt += "\n\n" + format_research_context(research_result)
    prompt += """

Produce file operations to implement the plan. For edits, provide `old_string` and `content`.
If you need additional research before implementing, set `research_request.query`.
Provide test commands as lists of shell arguments (e.g. [["pytest", "tests/unit"]]).
"""
    return prompt


def _apply_operations(code_tools: CodeTools, operations: list[FileOperation]) -> None:
    """Apply a list of file operations to the worktree."""
    for op in operations:
        try:
            if op.operation == "create":
                code_tools.write_file(op.path, op.content or "")
            elif op.operation == "edit":
                code_tools.edit_file(op.path, op.old_string or "", op.content or "")
            elif op.operation == "delete":
                target = code_tools._resolve(op.path)
                target.unlink(missing_ok=True)
                logger.info("Deleted %s", target)
            else:
                logger.warning("Unknown operation: %s", op.operation)
        except Exception as exc:
            logger.error("Operation failed for %s: %s", op.path, exc)
            raise
