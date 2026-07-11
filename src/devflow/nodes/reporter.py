"""Reporter node: create PR and produce corporate report."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from devflow.config import Config
from devflow.llm_factory import build_llm
from devflow.mcp.factory import build_task_source
from devflow.notifications.factory import build_notification_channels
from devflow.schemas import ReporterResponse
from devflow.state import CheckerReport, FinalVerdict, Task, WorkflowError, WorkflowState
from devflow.todo import TodoItem, mark_done
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

        # Build a Markdown report to publish to notification channels.
        report_text = _build_report_markdown(
            task=task,
            response=response,
            verdict=verdict,
            reports=reports,
            branch=branch,
        )

        # If the workflow reached the reporter because of an error, surface it.
        error = state.get("error")
        if error is not None:
            _publish_to_channels(app_cfg, _build_error_markdown(task, error))

        # Publish to configured channels.
        pr_url = _placeholder_pr_url(app_cfg, branch)
        report_url = _publish_to_channels(app_cfg, report_text)

        # Update task status in the source tracker if supported.
        updated_task = _update_task_status(app_cfg, task, verdict)

        # Write a short inline result into the originating TODO.md line so the
        # backlog stays in sync without a separate report file.
        _record_todo_result(app_cfg, state, response, verdict)

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


def _build_report_markdown(
    *,
    task: Task,
    response: ReporterResponse,
    verdict: FinalVerdict | None,
    reports: list[CheckerReport],
    branch: str | None,
) -> str:
    """Compose the Markdown report published to notification channels."""
    lines = [
        f"*Workflow report: {task.title}*",
        "",
        f"Task: `{task.id}`",
        f"Branch: `{branch or '-'}`",
        f"Verdict: *{verdict.value if verdict else 'unknown'}*",
        "",
        f"*PR:* {response.pr_title}",
        response.pr_description,
        "",
    ]
    if reports:
        lines.append("*Checker reports:*")
        for report in reports:
            icon = "✅" if report.verdict.value == "approve" else "⚠️"
            lines.append(f"{icon} {report.agent_name}: {report.summary}")
        lines.append("")
    lines.append(f"```\n{response.corporate_report}\n```")
    return "\n".join(lines)


def _build_error_markdown(task: Task, error: WorkflowError) -> str:
    """Compose a Markdown error notification."""
    return (
        f"⚠️ *Workflow error on task {task.id}*\n"
        f"*{task.title}*\n\n"
        f"Node: `{error.node}`\n"
        f"Error: {error.message}"
    )


def _publish_to_channels(app_cfg: Config, message: str) -> str | None:
    """Publish ``message`` to all configured notification channels.

    Returns the URL/id of the last successfully reporting channel (preferring
    non-console channels), or ``None`` if nothing was published. Individual
    channel failures are logged but do not abort the others.
    """
    channels = build_notification_channels(app_cfg.workflow)
    last_url: str | None = None
    for channel in channels:
        try:
            url = channel.send(message, parse_mode="Markdown")
            # Prefer a real channel URL over the console placeholder.
            if url != "console" or last_url is None:
                last_url = url
        except Exception as exc:
            logger.warning("Notification channel '%s' failed: %s", channel.name, exc)
        finally:
            try:
                channel.close()
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Channel '%s' close failed: %s", channel.name, exc)
    return last_url


def _placeholder_pr_url(app_cfg: Config, branch: str | None) -> str | None:
    """Return a placeholder PR URL for stub channels (github/gitlab).

    Real forge integrations are not implemented; this preserves the previous
    behaviour where a synthetic URL was produced so downstream code and logs
    still reference the branch.
    """
    if not branch:
        return None
    channels = app_cfg.workflow.corporate_report_channels
    if "github" in channels:
        return f"https://github.example.com/pr/{branch}"
    if "gitlab" in channels:
        return f"https://gitlab.example.com/merge_requests/{branch}"
    return None


# A short inline result is more useful in TODO.md than the full corporate
# report; cap its length so lines stay scannable.
_TODO_RESULT_MAX_LEN = 200


def _record_todo_result(
    app_cfg: Config,
    state: WorkflowState,
    response: ReporterResponse,
    verdict: FinalVerdict | None,
) -> None:
    """Write a one-line completion result back into the originating TODO entry.

    Failures are logged but never propagated: reporting should not fail the
    workflow because the TODO file could not be updated.
    """
    todo_item = state.get("todo_item")
    if not isinstance(todo_item, TodoItem):
        return
    todo_path = Path(app_cfg.workflow.todo_path)
    result_text = (response.corporate_report or "").strip()
    if len(result_text) > _TODO_RESULT_MAX_LEN:
        result_text = result_text[:_TODO_RESULT_MAX_LEN].rstrip() + "…"
    kind = "done" if verdict == FinalVerdict.APPROVE else "problem"
    try:
        mark_done(todo_path, todo_item, result_text, kind=kind)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to record TODO result for %s: %s", todo_item.task_id(), exc)
