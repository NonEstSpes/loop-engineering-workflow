"""Unit tests for EodHandler."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from devflow.batch.eod_handler import EodHandler
from devflow.batch.models import BatchEntry
from devflow.batch.store import BatchStore
from devflow.config import Config
from devflow.daemon.events import EventBus
from devflow.forge.base import MRInfo
from devflow.schemas import ReporterResponse
from devflow.state import FinalVerdict


def _make_entry(task_id: str) -> BatchEntry:
    return BatchEntry(
        task_id=task_id,
        task_title=f"Task {task_id}",
        branch_name=f"devflow/{task_id}/abc",
        worktree_path="/tmp/repo-wt",
        diff="d",
        plan_summary="s",
        plan_steps=[],
        checker_reports=[],
        self_review_notes="",
        final_verdict=FinalVerdict.APPROVE,
        reporter_artifacts=ReporterResponse(
            pr_title="t", pr_description="d", corporate_report="r", commit_message="c"
        ),
        created_at="2026-07-13T10:00:00Z",
    )


class StubForge:
    name = "stub"

    def push(self, branch, target, repo_path):
        return "sha-stub"

    def create_mr(self, branch, target, title, description):
        return MRInfo(url="https://example.com/mr/1", number=1)

    def healthcheck(self):
        return True

    def close(self):
        pass


@pytest.fixture
def handler(
    tmp_path: Path, mock_config: Config, monkeypatch: pytest.MonkeyPatch
) -> EodHandler:
    store = BatchStore(tmp_path / "batch_store.db")
    monkeypatch.setattr(
        "devflow.batch.publisher.build_forge_backend", lambda wf: StubForge()
    )
    monkeypatch.setattr(
        "devflow.batch.publisher._publish_to_channels", lambda cfg, msg: "console"
    )
    fake_source = MagicMock()
    fake_source.update_task_status = MagicMock()
    fake_source.close = lambda: None
    monkeypatch.setattr(
        "devflow.batch.publisher.build_task_source", lambda wf: fake_source
    )
    bus = EventBus()
    return EodHandler(mock_config, store, bus, repo_path="/tmp/repo")


def test_list_pending_returns_from_store(handler: EodHandler) -> None:
    """list_pending() delegates to the store."""
    handler._store.add(_make_entry("T-1"))
    handler._store.add(_make_entry("T-2"))
    pending = handler.list_pending()
    assert len(pending) == 2
    assert pending[0].task_id == "T-1"


def test_publish_selected_publishes_matching(handler: EodHandler) -> None:
    """publish_selected() publishes entries whose task_id is in the list."""
    handler._store.add(_make_entry("T-1"))
    handler._store.add(_make_entry("T-2"))
    handler._store.add(_make_entry("T-3"))

    result = handler.publish_selected(["T-1", "T-3"])

    assert set(result["published"]) == {"T-1", "T-3"}
    assert result["failed"] == []
    assert result["skipped"] == ["T-2"]
    # Store: only T-2 remains pending.
    assert handler._store.count_pending() == 1


def test_publish_selected_empty_list_publishes_all(handler: EodHandler) -> None:
    """publish_selected([]) publishes all pending entries."""
    handler._store.add(_make_entry("T-1"))
    handler._store.add(_make_entry("T-2"))

    result = handler.publish_selected([])

    assert set(result["published"]) == {"T-1", "T-2"}
    assert handler._store.count_pending() == 0


def test_publish_selected_unknown_task_id_is_skipped(handler: EodHandler) -> None:
    """publish_selected() with an unknown task_id lists it as skipped."""
    handler._store.add(_make_entry("T-1"))
    result = handler.publish_selected(["T-1", "T-UNKNOWN"])
    assert "T-1" in result["published"]
    assert "T-UNKNOWN" in result["skipped"]


def test_publish_selected_records_failed(handler: EodHandler, monkeypatch) -> None:
    """A publish that raises is recorded in failed, others still proceed."""

    class FailingPublisher:
        def __init__(self, *a, **kw):
            pass

        def publish(self, entry):
            raise RuntimeError("boom")

    monkeypatch.setattr(handler, "build_publisher", lambda: FailingPublisher())
    handler._store.add(_make_entry("T-1"))
    result = handler.publish_selected(["T-1"])
    assert result["published"] == []
    assert result["failed"] == ["T-1"]


async def test_finalize_publishes_eod_ready_event(handler: EodHandler) -> None:
    """finalize() returns pending entries and publishes an eod.ready event."""
    handler._store.add(_make_entry("T-1"))
    queue = await handler._bus.subscribe("eod")
    pending = handler.finalize()
    assert len(pending) == 1
    msg = await queue.get()
    assert msg["event"] == "eod.ready"
    assert msg["pending_count"] == 1


def test_finalize_is_sync_return(handler: EodHandler) -> None:
    """finalize() returns a list synchronously (event publish is best-effort)."""
    handler._store.add(_make_entry("T-1"))
    pending = handler.finalize()
    assert isinstance(pending, list)
    assert len(pending) == 1
