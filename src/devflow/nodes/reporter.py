"""Reporter node: create PR and produce corporate report."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Any

from devflow.config import Config
from devflow.forge.factory import build_forge_backend
from devflow.llm_factory import build_llm
from devflow.mcp.factory import build_task_source
from devflow.notifications.factory import build_notification_channels
from devflow.schemas import ReporterResponse
from devflow.state import CheckerReport, FinalVerdict, Task, WorkflowError, WorkflowState
from devflow.todo import TodoItem, mark_done
from devflow.utils.structured_llm import call_structured

logger = logging.getLogger(__name__)


def reporter_node(
    state: WorkflowState, *, app_cfg: Config, prepare_only: bool = False
) -> dict[str, Any]:
    """Generate PR description and corporate report; optionally publish them.

    Args:
        state: The workflow state carrying the task, plan, diff, and reports.
        app_cfg: The application config (agents, workflow, notifications).
        prepare_only: When True, only the ``record_todo`` action runs locally;
            publishing, tracker updates, push, and create_mr are deferred to
            the batch-publish stage. (Not yet wired through the graph; reserved
            for future end_of_day per-task local-only runs.)
    """
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

        # Build the report markdown for notification channels.
        report_text = _build_report_markdown(
            task=task,
            response=response,
            verdict=verdict,
            reports=reports,
            branch=branch,
        )

        # Surface errors via notification channels (best-effort).
        error = state.get("error")
        if error is not None:
            _publish_to_channels(app_cfg, _build_error_markdown(task, error))

        # Execute config-driven actions.
        action_results = _execute_actions(
            app_cfg=app_cfg,
            state=state,
            task=task,
            response=response,
            verdict=verdict,
            report_text=report_text,
            branch=branch,
            prepare_only=prepare_only,
        )

        logger.info("Reporter finished for task %s", task.id)
        return {
            "pr_url": action_results.get("pr_url"),
            "mr_url": action_results.get("mr_url"),
            "pushed_sha": action_results.get("pushed_sha"),
            "report_url": action_results.get("report_url"),
            "reporter_artifacts": response,
            "task": action_results.get("task", task),
            "logs": [
                f"reporter: PR '{response.pr_title}'",
                "reporter: corporate report generated",
            ]
            + action_results.get("action_logs", []),
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


def _execute_actions(
    *,
    app_cfg: Config,
    state: WorkflowState,
    task: Task,
    response: ReporterResponse,
    verdict: FinalVerdict | None,
    report_text: str,
    branch: str | None,
    prepare_only: bool = False,
) -> dict[str, Any]:
    """Execute config-driven publication actions.

    Each action is independent: a failure in one does not abort the others
    (same pattern as _publish_to_channels). Returns a dict of results.
    """
    forge_cfg = app_cfg.workflow.forge
    actions = forge_cfg.actions
    # In prepare-only mode (end_of_day per-task), only record_todo runs
    # locally. Publishing, tracker updates, push, and create_mr are deferred
    # to the batch-publish stage.
    if prepare_only:
        actions = ["record_todo"] if "record_todo" in actions else []
    results: dict[str, Any] = {"action_logs": []}
    forge: Any = None

    try:
        forge = build_forge_backend(app_cfg.workflow)
    except Exception as exc:
        logger.warning("Failed to build forge backend: %s", exc)

    # publish_report: send the corporate report to notification channels.
    if "publish_report" in actions:
        try:
            report_url = _publish_to_channels(app_cfg, report_text)
            results["report_url"] = report_url
        except Exception as exc:
            logger.warning("publish_report action failed: %s", exc)

    # update_tracker: update task status in the external tracker.
    if "update_tracker" in actions:
        try:
            updated_task = _update_task_status(app_cfg, task, verdict)
            results["task"] = updated_task
        except Exception as exc:
            logger.warning("update_tracker action failed: %s", exc)

    # record_todo: write result back into TODO.md.
    if "record_todo" in actions:
        try:
            _record_todo_result(app_cfg, state, response, verdict)
        except Exception as exc:
            logger.warning("record_todo action failed: %s", exc)

    # push: push the branch to the remote via forge backend.
    if "push" in actions and forge is not None and branch:
        try:
            repo_path = state.get("worktree_path") or "."
            sha = forge.push(branch, forge_cfg.target_branch, repo_path)
            results["pushed_sha"] = sha
            results["action_logs"].append(f"reporter: pushed {branch} -> {sha[:8]}")
        except Exception as exc:
            logger.warning("push action failed: %s", exc)

    # create_mr: create a merge request via forge backend.
    if "create_mr" in actions and forge is not None and branch:
        try:
            mr_info = forge.create_mr(
                branch=branch,
                target=forge_cfg.target_branch,
                title=response.pr_title,
                description=response.pr_description,
            )
            results["mr_url"] = mr_info.url
            results["pr_url"] = mr_info.url  # backward compat
            results["action_logs"].append(f"reporter: MR created {mr_info.url}")
        except Exception as exc:
            logger.warning("create_mr action failed: %s", exc)

    # Close the forge backend if it was opened.
    if forge is not None:
        with contextlib.suppress(Exception):
            forge.close()

    return results


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
