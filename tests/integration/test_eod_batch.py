"""Integration test: end-to-end EOD batch flow.

Simulates the end_of_day lifecycle:
1. Two BatchEntries are accumulated (as the runner would after per-task runs).
2. EodHandler.publish_selected([]) publishes all pending.
3. The fake forge records push + create_mr calls.
4. The store reflects PUBLISHED status for both.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from devflow.batch.eod_handler import EodHandler
from devflow.batch.models import BatchEntry, BatchStatus
from devflow.batch.store import BatchStore
from devflow.config import Config
from devflow.daemon.events import EventBus
from devflow.forge.base import MRInfo
from devflow.schemas import ReporterResponse
from devflow.state import CheckerReport, CheckerVerdict, FinalVerdict


def _make_entry(task_id: str, branch: str) -> BatchEntry:
    return BatchEntry(
        task_id=task_id,
        task_title=f"Task {task_id}",
        branch_name=branch,
        worktree_path="/tmp/repo-wt",
        diff=f"diff for {task_id}",
        plan_summary="summary",
        plan_steps=["step 1"],
        checker_reports=[
            CheckerReport(
                agent_name="checker_a",
                verdict=CheckerVerdict.APPROVE,
                summary="ok",
            )
        ],
        self_review_notes="",
        final_verdict=FinalVerdict.APPROVE,
        reporter_artifacts=ReporterResponse(
            pr_title=f"feat: {task_id}",
            pr_description="description",
            corporate_report="report",
            commit_message=f"feat: {task_id}",
        ),
        created_at="2026-07-13T10:00:00Z",
    )


class RecordingForge:
    name = "recording"

    def __init__(self) -> None:
        self.pushed: list[str] = []
        self.mrs: list[str] = []

    def push(self, branch: str, target: str, repo_path: str) -> str:
        self.pushed.append(branch)
        return f"sha-{branch}"

    def create_mr(self, branch: str, target: str, title: str, description: str) -> MRInfo:
        self.mrs.append(branch)
        return MRInfo(url=f"https://example.com/mr/{branch}", number=len(self.mrs))

    def healthcheck(self) -> bool:
        return True

    def close(self) -> None:
        pass


def test_eod_batch_publish_all(
    tmp_path: Path, mock_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full EOD batch: accumulate 2 entries, publish all, verify forge + store."""
    store = BatchStore(tmp_path / "batch_store.db")
    store.add(_make_entry("T-1", "devflow/T-1/aaa"))
    store.add(_make_entry("T-2", "devflow/T-2/bbb"))
    assert store.count_pending() == 2

    forge = RecordingForge()
    monkeypatch.setattr("devflow.batch.publisher.build_forge_backend", lambda wf: forge)
    monkeypatch.setattr(
        "devflow.batch.publisher._publish_to_channels", lambda cfg, msg: "console"
    )
    fake_source = MagicMock()
    fake_source.update_task_status = MagicMock()
    fake_source.close = lambda: None
    monkeypatch.setattr(
        "devflow.batch.publisher.build_task_source", lambda wf: fake_source
    )

    mock_config.workflow.forge.provider = "github"
    mock_config.workflow.forge.target_branch = "main"

    handler = EodHandler(mock_config, store, EventBus(), repo_path=str(tmp_path))
    result = handler.publish_selected([])

    assert set(result["published"]) == {"T-1", "T-2"}
    assert result["failed"] == []
    # Forge pushed both branches and created MRs for both.
    assert set(forge.pushed) == {"devflow/T-1/aaa", "devflow/T-2/bbb"}
    assert set(forge.mrs) == {"devflow/T-1/aaa", "devflow/T-2/bbb"}
    # Tracker updated for both.
    assert fake_source.update_task_status.call_count == 2
    # Store: no pending left; both published.
    assert store.count_pending() == 0
    published = store.list_all(status=BatchStatus.PUBLISHED)
    assert len(published) == 2
    for entry in published:
        assert entry.pushed_sha is not None
        assert entry.mr_url is not None
    store.close()


def test_eod_batch_publish_subset_then_finalize(
    tmp_path: Path, mock_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publish a subset, then finalize reports only the remaining pending."""
    store = BatchStore(tmp_path / "batch_store.db")
    store.add(_make_entry("T-1", "devflow/T-1/aaa"))
    store.add(_make_entry("T-2", "devflow/T-2/bbb"))
    store.add(_make_entry("T-3", "devflow/T-3/ccc"))

    forge = RecordingForge()
    monkeypatch.setattr("devflow.batch.publisher.build_forge_backend", lambda wf: forge)
    monkeypatch.setattr(
        "devflow.batch.publisher._publish_to_channels", lambda cfg, msg: "console"
    )
    fake_source = MagicMock()
    fake_source.update_task_status = MagicMock()
    fake_source.close = lambda: None
    monkeypatch.setattr(
        "devflow.batch.publisher.build_task_source", lambda wf: fake_source
    )

    mock_config.workflow.forge.provider = "github"
    handler = EodHandler(mock_config, store, EventBus(), repo_path=str(tmp_path))

    # Publish only T-1 and T-3.
    result = handler.publish_selected(["T-1", "T-3"])
    assert set(result["published"]) == {"T-1", "T-3"}
    assert "T-2" in result["skipped"]
    assert store.count_pending() == 1

    # Finalize returns the remaining pending (T-2).
    pending = handler.finalize()
    assert len(pending) == 1
    assert pending[0].task_id == "T-2"
    store.close()


async def test_eod_finalize_emits_event(
    tmp_path: Path, mock_config: Config
) -> None:
    """finalize() publishes an eod.ready event on the event bus."""
    store = BatchStore(tmp_path / "batch_store.db")
    store.add(_make_entry("T-1", "devflow/T-1/aaa"))
    bus = EventBus()
    handler = EodHandler(mock_config, store, bus, repo_path=str(tmp_path))

    queue = await bus.subscribe("eod")
    handler.finalize()
    msg = await queue.get()
    assert msg["event"] == "eod.ready"
    assert msg["pending_count"] == 1
    store.close()
