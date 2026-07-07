"""Shared state models for the development workflow."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, TypedDict, TypeVar

from pydantic import BaseModel, Field

from devflow.schemas import Plan, ResearchRequest, ResearchResult

T = TypeVar("T")


def _add_reducer(existing: list[T] | None, updates: list[T] | None) -> list[T]:
    """Append updates to an existing list (LangGraph reducer)."""
    existing = existing or []
    updates = updates or []
    return existing + updates


def _max_reducer(existing: int | None, update: int | None) -> int:
    """Return the maximum of current and updated value."""
    return max(existing or 0, update or 0)


class Task(BaseModel):
    """A task fetched from an external tracker."""

    id: str
    title: str
    description: str
    status: str = "open"
    metadata: dict[str, Any] = Field(default_factory=dict)


class CheckerVerdict(StrEnum):
    """Verdict a single checker subagent can return."""

    APPROVE = "approve"
    REJECT = "reject"
    CONDITIONAL = "conditional"


class CheckerReport(BaseModel):
    """Report from a single checker subagent."""

    agent_name: str
    verdict: CheckerVerdict
    summary: str
    findings: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class FinalVerdict(StrEnum):
    """Aggregated final verdict after all checkers."""

    APPROVE = "approve"
    REJECT = "reject"
    CONDITIONAL = "conditional"
    ESCALATE = "escalate"


class WorkflowError(BaseModel):
    """Error captured during workflow execution."""

    node: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class WorkflowState(TypedDict, total=False):
    """LangGraph state dictionary.

    All values are optional because different nodes populate them over time.
    Lists use reducers so that parallel checkers can append their reports.
    """

    task: Task | None
    plan: Plan | None
    plan_approved: bool | None
    worktree_path: str | None
    branch_name: str | None
    diff: str | None
    self_review_notes: str | None
    checker_agent: str | None
    checker_reports: Annotated[list[CheckerReport], _add_reducer]
    final_verdict: FinalVerdict | None
    rework_count: Annotated[int, _max_reducer]
    pr_url: str | None
    report_url: str | None
    error: WorkflowError | None
    logs: Annotated[list[str], _add_reducer]

    # On-demand research fields
    research_request: ResearchRequest | None
    last_research_result: ResearchResult | None
    research_results: Annotated[list[ResearchResult], _add_reducer]
    research_call_count: Annotated[int, _max_reducer]
