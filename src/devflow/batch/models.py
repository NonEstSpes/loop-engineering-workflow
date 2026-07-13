"""Pydantic models for the end-of-day batch store."""

from __future__ import annotations

from pydantic import BaseModel

from devflow.schemas import ReporterResponse
from devflow.state import CheckerReport, FinalVerdict


class BatchStatus:
    """Status constants for a BatchEntry lifecycle."""

    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"


class BatchEntry(BaseModel):
    """A single task's accumulated work, awaiting end-of-day batch publish."""

    id: int | None = None
    task_id: str
    task_title: str
    branch_name: str
    worktree_path: str
    diff: str
    plan_summary: str
    plan_steps: list[str]
    checker_reports: list[CheckerReport]
    self_review_notes: str
    final_verdict: FinalVerdict | None
    reporter_artifacts: ReporterResponse
    status: str = BatchStatus.PENDING_REVIEW
    created_at: str
    published_at: str | None = None
    mr_url: str | None = None
    pushed_sha: str | None = None
    rejection_reason: str | None = None
