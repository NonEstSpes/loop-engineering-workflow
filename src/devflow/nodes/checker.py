"""Checker node and dispatcher: run independent audit subagents in parallel."""

from __future__ import annotations

import logging
from typing import Any

from langgraph.types import Send

from devflow.config import Config
from devflow.llm_factory import build_llm
from devflow.state import (
    CheckerReport,
    CheckerVerdict,
    FinalVerdict,
    WorkflowError,
    WorkflowState,
)
from devflow.utils.structured_llm import call_structured

logger = logging.getLogger(__name__)

DEFAULT_CHECKERS = ["checker_a", "checker_b", "checker_c"]


def checker_dispatcher(state: WorkflowState) -> list[Send] | str:
    """Dispatch to checker subagents in parallel, or route to reporter on error."""
    if state.get("error"):
        return "reporter"
    task = state.get("task")
    diff = state.get("diff")
    self_review_notes = state.get("self_review_notes")
    if task is None or diff is None:
        return "reporter"
    return [
        Send(
            "run_checker",
            {
                "checker_agent": name,
                "task": task,
                "diff": diff,
                "self_review_notes": self_review_notes,
            },
        )
        for name in DEFAULT_CHECKERS
    ]


def run_checker_node(state: WorkflowState, *, app_cfg: Config) -> dict[str, Any]:
    """Run a single checker subagent and append its report."""
    agent_name = state.get("checker_agent")
    if agent_name is None:
        return {
            "error": WorkflowError(node="run_checker", message="checker_agent not set"),
            "logs": ["run_checker: error - checker_agent not set"],
        }

    task = state.get("task")
    diff = state.get("diff")
    self_review_notes = state.get("self_review_notes") or "No self-review notes"
    if task is None or diff is None:
        return {
            "error": WorkflowError(
                node=f"run_checker:{agent_name}",
                message="Missing task or diff in state",
            ),
            "logs": [f"run_checker:{agent_name}: error - missing task or diff"],
        }

    agent_cfg = app_cfg.agents.get(agent_name)
    if agent_cfg is None:
        return {
            "error": WorkflowError(
                node=f"run_checker:{agent_name}",
                message=f"Checker agent config not found: {agent_name}",
            ),
            "logs": [f"run_checker:{agent_name}: error - agent config not found"],
        }

    try:
        llm = build_llm(agent_cfg, app_cfg)
        user_prompt = f"""Task ID: {task.id}
Title: {task.title}
Description:
{task.description}

Self-review notes:
{self_review_notes}

Diff:
```diff
{diff}
```
"""
        response = call_structured(llm, agent_cfg.system_prompt, user_prompt, CheckerReport)
        response.agent_name = agent_name
        logger.info("Checker %s verdict: %s", agent_name, response.verdict)
        return {
            "checker_reports": [response],
            "logs": [f"checker {agent_name}: {response.verdict}"],
        }
    except Exception as exc:
        logger.exception("Checker %s failed", agent_name)
        fallback = CheckerReport(
            agent_name=agent_name,
            verdict=CheckerVerdict.REJECT,
            summary=f"Checker failed: {exc}",
        )
        return {
            "checker_reports": [fallback],
            "error": WorkflowError(node=f"run_checker:{agent_name}", message=str(exc)),
            "logs": [f"checker {agent_name}: error {exc}"],
        }


def aggregate_checker_node(state: WorkflowState, *, app_cfg: Config) -> dict[str, Any]:
    """Aggregate checker reports into a final verdict.

    The rework_count is incremented here when the verdict requires another pass
    (REJECT or CONDITIONAL) so that the graph router can decide whether to send
    the workflow back to the maker or to escalate/reporter.
    """
    reports = state.get("checker_reports", [])
    if not reports:
        logger.warning("No checker reports found, escalating")
        return {
            "final_verdict": FinalVerdict.ESCALATE,
            "logs": ["aggregate_checker: no reports, escalating"],
        }

    verdicts = [r.verdict for r in reports]
    if any(v == CheckerVerdict.REJECT for v in verdicts):
        final = FinalVerdict.REJECT
    elif all(v == CheckerVerdict.APPROVE for v in verdicts):
        final = FinalVerdict.APPROVE
    else:
        final = FinalVerdict.CONDITIONAL

    rework_count = state.get("rework_count", 0)
    max_rework = app_cfg.workflow.max_rework_iterations

    # Escalate if we have exhausted rework iterations and still get a hard reject.
    if final == FinalVerdict.REJECT and rework_count >= max_rework:
        final = FinalVerdict.ESCALATE

    # Increment rework counter whenever the workflow needs another maker pass.
    new_rework_count = rework_count
    if final in {FinalVerdict.REJECT, FinalVerdict.CONDITIONAL}:
        new_rework_count = rework_count + 1

    logger.info("Aggregated verdict: %s from %d reports", final.value, len(reports))
    return {
        "final_verdict": final,
        "rework_count": new_rework_count,
        "logs": [f"aggregate_checker: {final.value} ({len(reports)} reports)"],
    }
