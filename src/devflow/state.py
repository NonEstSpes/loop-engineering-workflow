"""Shared state models for the development workflow."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, TypedDict, TypeVar

from pydantic import BaseModel, Field

from devflow.schemas import Plan, ResearchRequest, ResearchResult

T = TypeVar("T")


# Checkbox markers driving the TODO.md task lifecycle.
CHECKBOX_OPEN = "[ ]"
CHECKBOX_IN_PROGRESS = "[~]"
CHECKBOX_DONE = "[x]"

# Sentinel id prefix for human-written TODO entries without a tracker reference.
LOCAL_ID_PREFIX = "local"


class TodoItem(BaseModel):
    """A single parsed line of ``TODO.md``.

    Lives in :mod:`devflow.state` (not :mod:`devflow.todo`) so that
    :class:`WorkflowState` can reference it without a circular import:
    ``todo`` imports ``Task`` from ``state``, so the data model must live in
    the lower-level module.

    Lines that do not look like a task entry (no checkbox, or no priority tag)
    are still parsed so the file round-trips unchanged; they are simply skipped
    during selection (``priority is None``).
    """

    raw_line: str
    line_no: int  # 1-based position in the file
    checkbox: str | None  # one of the CHECKBOX_* constants, or None for non-task lines
    priority: int | None  # 0..5, or None when no #rX tag
    task_ref: str | None  # tracker id without '#', e.g. "251977"
    url: str | None
    title: str
    result: str | None  # inline result suffix, if the line already carries one

    @property
    def is_task(self) -> bool:
        """True for lines the orchestrator can act on (valid checkbox)."""
        return self.checkbox is not None

    @property
    def is_link(self) -> bool:
        """True when the entry references an external tracker id (needs hydration).

        Any ``task_ref`` (with or without a URL) is treated as a tracker
        reference and hydrated via ``get_task_details``; the URL is purely a
        convenience for human readers of the file.
        """
        return self.task_ref is not None

    @property
    def is_actionable(self) -> bool:
        """True when the item is selectable: task + has priority + not done."""
        return self.is_task and self.priority is not None

    def task_id(self) -> str:
        """Stable id for this TODO entry used to build a :class:`Task`."""
        if self.task_ref is not None:
            return self.task_ref
        return f"{LOCAL_ID_PREFIX}-{self.line_no}"


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
    # The TODO.md entry the orchestrator picked for this run. The reporter
    # uses it to write the inline result back into the same line.
    todo_item: TodoItem | None
    plan: Plan | None
    plan_approved: bool | None
    publish_approved: bool | None
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
