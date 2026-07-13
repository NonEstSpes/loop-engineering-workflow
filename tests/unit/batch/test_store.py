"""Unit tests for BatchStore SQLite CRUD."""

from __future__ import annotations

from pathlib import Path

from devflow.batch.models import BatchEntry, BatchStatus
from devflow.batch.store import BatchStore
from devflow.schemas import ReporterResponse
from devflow.state import CheckerReport, CheckerVerdict, FinalVerdict


def _make_entry(task_id: str = "T-1") -> BatchEntry:
    return BatchEntry(
        task_id=task_id,
        task_title=f"Task {task_id}",
        branch_name=f"devflow/{task_id}/abc",
        worktree_path="/tmp/repo-wt",
        diff="diff content",
        plan_summary="summary",
        plan_steps=["step 1", "step 2"],
        checker_reports=[
            CheckerReport(
                agent_name="checker_a",
                verdict=CheckerVerdict.APPROVE,
                summary="ok",
            )
        ],
        self_review_notes="looks fine",
        final_verdict=FinalVerdict.APPROVE,
        reporter_artifacts=ReporterResponse(
            pr_title="feat: x",
            pr_description="desc",
            corporate_report="report",
            commit_message="feat: x",
        ),
        created_at="2026-07-13T10:00:00Z",
    )


def test_store_creates_db_file(tmp_path: Path) -> None:
    """BatchStore creates the SQLite file on init."""
    db_path = tmp_path / "batch_store.db"
    assert not db_path.exists()
    store = BatchStore(db_path)
    assert db_path.exists()
    store.close()


def test_add_returns_id(tmp_path: Path) -> None:
    """add() inserts an entry and returns a positive id."""
    store = BatchStore(tmp_path / "batch_store.db")
    try:
        entry = _make_entry()
        entry_id = store.add(entry)
        assert entry_id > 0
    finally:
        store.close()


def test_add_assigns_sequential_ids(tmp_path: Path) -> None:
    """Sequential adds produce increasing ids."""
    store = BatchStore(tmp_path / "batch_store.db")
    try:
        id1 = store.add(_make_entry("T-1"))
        id2 = store.add(_make_entry("T-2"))
        assert id2 == id1 + 1
    finally:
        store.close()


def test_get_entry_round_trip(tmp_path: Path) -> None:
    """get_entry() returns the stored entry with all fields intact."""
    store = BatchStore(tmp_path / "batch_store.db")
    try:
        entry_id = store.add(_make_entry())
        fetched = store.get_entry(entry_id)
        assert fetched is not None
        assert fetched.task_id == "T-1"
        assert fetched.status == BatchStatus.PENDING_REVIEW
        assert fetched.plan_steps == ["step 1", "step 2"]
        assert len(fetched.checker_reports) == 1
        assert fetched.reporter_artifacts.pr_title == "feat: x"
        assert fetched.final_verdict == FinalVerdict.APPROVE
    finally:
        store.close()


def test_get_entry_returns_none_for_unknown(tmp_path: Path) -> None:
    """get_entry() returns None for a non-existent id."""
    store = BatchStore(tmp_path / "batch_store.db")
    try:
        assert store.get_entry(999) is None
    finally:
        store.close()


def test_get_pending_returns_pending_only(tmp_path: Path) -> None:
    """get_pending() returns only pending_review entries, oldest first."""
    store = BatchStore(tmp_path / "batch_store.db")
    try:
        store.add(_make_entry("T-1"))
        id2 = store.add(_make_entry("T-2"))
        store.add(_make_entry("T-3"))
        # Mark T-2 as published — should not appear in pending.
        store.update_status(id2, BatchStatus.PUBLISHED)
        pending = store.get_pending()
        assert len(pending) == 2
        assert all(e.status == BatchStatus.PENDING_REVIEW for e in pending)
        assert pending[0].task_id == "T-1"
        assert pending[1].task_id == "T-3"
    finally:
        store.close()


def test_count_pending(tmp_path: Path) -> None:
    """count_pending() reflects the current pending count."""
    store = BatchStore(tmp_path / "batch_store.db")
    try:
        assert store.count_pending() == 0
        store.add(_make_entry("T-1"))
        store.add(_make_entry("T-2"))
        assert store.count_pending() == 2
    finally:
        store.close()


def test_update_status_to_published(tmp_path: Path) -> None:
    """update_status() sets published + mr_url + pushed_sha."""
    store = BatchStore(tmp_path / "batch_store.db")
    try:
        entry_id = store.add(_make_entry())
        ok = store.update_status(
            entry_id,
            BatchStatus.PUBLISHED,
            mr_url="https://example.com/mr/1",
            pushed_sha="abc123",
        )
        assert ok is True
        fetched = store.get_entry(entry_id)
        assert fetched is not None
        assert fetched.status == BatchStatus.PUBLISHED
        assert fetched.mr_url == "https://example.com/mr/1"
        assert fetched.pushed_sha == "abc123"
    finally:
        store.close()


def test_update_status_to_rejected(tmp_path: Path) -> None:
    """update_status() sets rejected + rejection_reason."""
    store = BatchStore(tmp_path / "batch_store.db")
    try:
        entry_id = store.add(_make_entry())
        ok = store.update_status(
            entry_id, BatchStatus.REJECTED, rejection_reason="scope mismatch"
        )
        assert ok is True
        fetched = store.get_entry(entry_id)
        assert fetched is not None
        assert fetched.status == BatchStatus.REJECTED
        assert fetched.rejection_reason == "scope mismatch"
    finally:
        store.close()


def test_update_status_unknown_id_returns_false(tmp_path: Path) -> None:
    """update_status() returns False for an unknown id."""
    store = BatchStore(tmp_path / "batch_store.db")
    try:
        ok = store.update_status(999, BatchStatus.PUBLISHED)
        assert ok is False
    finally:
        store.close()


def test_get_by_task(tmp_path: Path) -> None:
    """get_by_task() returns all entries for a task id."""
    store = BatchStore(tmp_path / "batch_store.db")
    try:
        store.add(_make_entry("T-1"))
        store.add(_make_entry("T-1"))
        store.add(_make_entry("T-2"))
        entries = store.get_by_task("T-1")
        assert len(entries) == 2
        assert all(e.task_id == "T-1" for e in entries)
    finally:
        store.close()


def test_list_all_filtered(tmp_path: Path) -> None:
    """list_all(status=) filters by status."""
    store = BatchStore(tmp_path / "batch_store.db")
    try:
        id1 = store.add(_make_entry("T-1"))
        store.add(_make_entry("T-2"))
        store.update_status(id1, BatchStatus.PUBLISHED)
        published = store.list_all(status=BatchStatus.PUBLISHED)
        assert len(published) == 1
        assert published[0].task_id == "T-1"
        all_entries = store.list_all()
        assert len(all_entries) == 2
    finally:
        store.close()


def test_store_persists_across_reopen(tmp_path: Path) -> None:
    """Entries survive close + reopen (SQLite persistence)."""
    db_path = tmp_path / "batch_store.db"
    store1 = BatchStore(db_path)
    entry_id = store1.add(_make_entry())
    store1.close()

    store2 = BatchStore(db_path)
    try:
        fetched = store2.get_entry(entry_id)
        assert fetched is not None
        assert fetched.task_id == "T-1"
        assert store2.count_pending() == 1
    finally:
        store2.close()
