"""Unit tests for BatchEntry and BatchStatus."""

from __future__ import annotations

from devflow.batch.models import BatchEntry, BatchStatus
from devflow.schemas import ReporterResponse
from devflow.state import CheckerReport, CheckerVerdict, FinalVerdict


def _artifacts() -> ReporterResponse:
    return ReporterResponse(
        pr_title="feat: add thing",
        pr_description="body",
        corporate_report="report text",
        commit_message="feat: add thing",
    )


def test_batch_status_constants() -> None:
    assert BatchStatus.PENDING_REVIEW == "pending_review"
    assert BatchStatus.APPROVED == "approved"
    assert BatchStatus.REJECTED == "rejected"
    assert BatchStatus.PUBLISHED == "published"


def test_batch_entry_minimal() -> None:
    """BatchEntry can be created with required fields only."""
    entry = BatchEntry(
        task_id="T-1",
        task_title="Do thing",
        branch_name="devflow/T-1/abc",
        worktree_path="/tmp/repo-wt",
        diff="--- a\n+++ b\n+x\n",
        plan_summary="summary",
        plan_steps=["step 1"],
        checker_reports=[],
        self_review_notes="",
        final_verdict=FinalVerdict.APPROVE,
        reporter_artifacts=_artifacts(),
        created_at="2026-07-13T10:00:00Z",
    )
    assert entry.id is None
    assert entry.status == BatchStatus.PENDING_REVIEW
    assert entry.published_at is None
    assert entry.mr_url is None
    assert entry.pushed_sha is None
    assert entry.rejection_reason is None


def test_batch_entry_with_checker_reports() -> None:
    """BatchEntry round-trips checker reports."""
    report = CheckerReport(
        agent_name="checker_a",
        verdict=CheckerVerdict.APPROVE,
        summary="looks good",
    )
    entry = BatchEntry(
        task_id="T-2",
        task_title="t",
        branch_name="b",
        worktree_path="/p",
        diff="d",
        plan_summary="s",
        plan_steps=[],
        checker_reports=[report],
        self_review_notes="",
        final_verdict=FinalVerdict.APPROVE,
        reporter_artifacts=_artifacts(),
        created_at="2026-07-13T10:00:00Z",
    )
    assert len(entry.checker_reports) == 1
    assert entry.checker_reports[0].agent_name == "checker_a"


def test_batch_entry_round_trip_published() -> None:
    """A fully-published entry carries all publish fields."""
    entry = BatchEntry(
        id=7,
        task_id="T-3",
        task_title="t",
        branch_name="b",
        worktree_path="/p",
        diff="d",
        plan_summary="s",
        plan_steps=[],
        checker_reports=[],
        self_review_notes="",
        final_verdict=FinalVerdict.APPROVE,
        reporter_artifacts=_artifacts(),
        status=BatchStatus.PUBLISHED,
        created_at="2026-07-13T10:00:00Z",
        published_at="2026-07-13T18:00:00Z",
        mr_url="https://example.com/mr/1",
        pushed_sha="abc123",
    )
    assert entry.status == BatchStatus.PUBLISHED
    assert entry.mr_url == "https://example.com/mr/1"
    assert entry.id == 7


def test_batch_entry_serialization_roundtrip() -> None:
    """BatchEntry serializes to dict and back via model_dump."""
    entry = BatchEntry(
        task_id="T-4",
        task_title="t",
        branch_name="b",
        worktree_path="/p",
        diff="d",
        plan_summary="s",
        plan_steps=["a", "b"],
        checker_reports=[],
        self_review_notes="",
        final_verdict=None,
        reporter_artifacts=_artifacts(),
        created_at="2026-07-13T10:00:00Z",
    )
    data = entry.model_dump()
    restored = BatchEntry(**data)
    assert restored.task_id == entry.task_id
    assert restored.plan_steps == ["a", "b"]
    assert restored.reporter_artifacts.pr_title == "feat: add thing"
