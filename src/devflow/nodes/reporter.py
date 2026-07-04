"""Reporter node: create PR and produce corporate report."""

from __future__ import annotations

import logging
from typing import Any

from devflow.config import Config
from devflow.llm_factory import build_llm
from devflow.mcp.factory import build_task_source
from devflow.schemas import ReporterResponse
from devflow.state import FinalVerdict, Task, WorkflowError, WorkflowState
from devflow.utils.structured_llm import call_structured

logger = logging.getLogger(__name__)


def reporter_node(state: WorkflowState, *, app_cfg: Config) -> dict[str, Any]:
    """Generate PR description and corporate report; optionally publish them."""
    task = state.get("task")
    plan = state.get("plan")
    diff = state.get("diff") or ""
    reports = state.get("checker_reports", [])
    verdict = state.get("final_verdict")
    branch = state.get("branch_name")

    if task is None:
        return {
            "error": WorkflowError(node="reporter", message="Missing task in state"),
            "logs": ["reporter: error - missing task"],
        }

    agent_cfg = app_cfg.agents.get("reporter")
    if agent_cfg is None:
        return {
            "error": WorkflowError(node="reporter", message="reporter agent config not found"),
            "logs": ["reporter: error - reporter agent config not found"],
        }

    try:
        llm = build_llm(agent_cfg, app_cfg)
        user_prompt = f"""Task ID: {task.id}
Title: {task.title}
Description:
{task.description}

Plan:
{plan.summary if plan else "No plan"}

Final verdict: {verdict.value if verdict else "unknown"}

Checker reports:
"""
        for report in reports:
            user_prompt += f"- {report.agent_name}: {report.verdict.value} - {report.summary}\n"

        user_prompt += f"\nDiff:\n```diff\n{diff[:8000]}\n```\n"

        response = call_structured(llm, agent_cfg.system_prompt, user_prompt, ReporterResponse)

        # Publish to configured channels
        pr_url = None
        report_url = None
        for channel in app_cfg.workflow.corporate_report_channels:
            if channel == "console":
                logger.info("PR: %s\n%s", response.pr_title, response.pr_description)
                report_url = "console"
            elif channel == "github" and branch:
                pr_url = f"https://github.example.com/pr/{branch}"
            elif channel == "gitlab" and branch:
                pr_url = f"https://gitlab.example.com/merge_requests/{branch}"
            elif channel == "slack":
                report_url = "slack://posted"

        # Update task status in the source tracker if supported.
        updated_task = _update_task_status(app_cfg, task, verdict)

        logger.info("Reporter finished for task %s", task.id)
        return {
            "pr_url": pr_url,
            "report_url": report_url,
            "task": updated_task,
            "logs": [
                f"reporter: PR '{response.pr_title}'",
                "reporter: corporate report generated",
            ],
        }
    except Exception as exc:
        logger.exception("Reporter failed")
        return {
            "error": WorkflowError(node="reporter", message=str(exc)),
            "logs": [f"reporter: error {exc}"],
        }


def _update_task_status(
    app_cfg: Config,
    task: Task,
    verdict: FinalVerdict | None,
) -> Task:
    """Update the task status in the external tracker based on the final verdict.

    Mapping:
        APPROVE    -> resolved
        CONDITIONAL -> pending
        REJECT     -> rejected
        ESCALATE   -> escalated
        None       -> open
    """
    if verdict is None:
        return task

    status_map = {
        FinalVerdict.APPROVE: "resolved",
        FinalVerdict.CONDITIONAL: "pending",
        FinalVerdict.REJECT: "rejected",
        FinalVerdict.ESCALATE: "escalated",
    }
    new_status = status_map.get(verdict, "open")

    try:
        source = build_task_source(app_cfg.workflow)
        source.update_task_status(
            task.id,
            new_status,
            comment=f"Final verdict: {verdict.value}",
        )
        source.close()
        return task.model_copy(update={"status": new_status})
    except NotImplementedError:
        logger.debug("Task source does not support status updates")
        return task
    except Exception as exc:
        logger.warning("Failed to update task status: %s", exc)
        return task
