"""Unit tests for BatchPublisher."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from devflow.batch.models import BatchEntry, BatchStatus
from devflow.batch.publisher import BatchPublisher
from devflow.batch.store import BatchStore
from devflow.config import Config
from devflow.forge.base import MRInfo
from devflow.schemas import ReporterResponse
from devflow.state import CheckerReport, CheckerVerdict, FinalVerdict


def _make_entry(task_id: str = "T-1") -> BatchEntry:
    return BatchEntry(
        task_id=task_id,
        task_title=f"Task {task_id}",
        branch_name=f"devflow/{task_id}/abc",
        worktree_path="/tmp/repo-wt",
        diff="diff",
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
            pr_title="feat: x",
            pr_description="desc",
            corporate_report="report",
            commit_message="feat: x",
        ),
        created_at="2026-07-13T10:00:00Z",
    )


class RecordingForge:
    """Fake forge backend recording push/create_mr calls."""

    name = "recording"

    def __init__(self) -> None:
        self.pushed: list[tuple[str, str, str]] = []
        self.mrs: list[dict[str, str]] = []

    def push(self, branch: str, target: str, repo_path: str) -> str:
        self.pushed.append((branch, target, repo_path))
        return "sha-published"

    def create_mr(self, branch: str, target: str, title: str, description: str) -> MRInfo:
        self.mrs.append(
            {"branch": branch, "target": target, "title": title, "description": description}
        )
        return MRInfo(url="https://example.com/mr/1", number=1)

    def healthcheck(self) -> bool:
        return True

    def close(self) -> None:
        pass


def test_publish_full_success(
    tmp_path, mock_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full publish: push + MR + report + tracker, entry marked published."""
    store = BatchStore(tmp_path / "batch_store.db")
    entry_id = store.add(_make_entry())
    entry = store.get_entry(entry_id)
    assert entry is not None
    entry.id = entry_id

    forge = RecordingForge()
    monkeypatch.setattr(
        "devflow.batch.publisher.build_forge_backend", lambda wf: forge
    )

    publish_called: list[bool] = []
    monkeypatch.setattr(
        "devflow.batch.publisher._publish_to_channels",
        lambda cfg, msg: publish_called.append(True) or "console",
    )

    tracker_called: list[str] = []
    fake_source = MagicMock()
    fake_source.update_task_status = (
        lambda task_id, status, comment=None: tracker_called.append(task_id)
    )
    fake_source.close = lambda: None
    monkeypatch.setattr(
        "devflow.batch.publisher.build_task_source", lambda wf: fake_source
    )

    publisher = BatchPublisher(mock_config, store, repo_path="/tmp/repo")
    published = publisher.publish(entry)

    assert forge.pushed == [("devflow/T-1/abc", "main", "/tmp/repo")]
    assert len(forge.mrs) == 1
    assert forge.mrs[0]["branch"] == "devflow/T-1/abc"
    assert publish_called == [True]
    assert tracker_called == ["T-1"]
    assert published.status == BatchStatus.PUBLISHED
    assert published.pushed_sha == "sha-published"
    assert published.mr_url == "https://example.com/mr/1"

    # Store reflects the update.
    fetched = store.get_entry(entry_id)
    assert fetched is not None
    assert fetched.status == BatchStatus.PUBLISHED
    store.close()


def test_publish_push_failure_keeps_pending(
    tmp_path, mock_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If push fails, the entry stays pending_review for retry."""
    store = BatchStore(tmp_path / "batch_store.db")
    entry_id = store.add(_make_entry())
    entry = store.get_entry(entry_id)
    assert entry is not None
    entry.id = entry_id

    class ExplodingForge(RecordingForge):
        def push(self, branch, target, repo_path):
            raise RuntimeError("network down")

    monkeypatch.setattr(
        "devflow.batch.publisher.build_forge_backend", lambda wf: ExplodingForge()
    )
    monkeypatch.setattr(
        "devflow.batch.publisher._publish_to_channels",
        lambda cfg, msg: "console",
    )
    fake_source = MagicMock()
    fake_source.update_task_status = MagicMock()
    fake_source.close = lambda: None
    monkeypatch.setattr(
        "devflow.batch.publisher.build_task_source", lambda wf: fake_source
    )

    publisher = BatchPublisher(mock_config, store, repo_path="/tmp/repo")
    result = publisher.publish(entry)

    assert result.status == BatchStatus.PENDING_REVIEW
    fetched = store.get_entry(entry_id)
    assert fetched is not None
    assert fetched.status == BatchStatus.PENDING_REVIEW
    store.close()


def test_publish_no_forge_skips_push_but_publishes_report(
    tmp_path, mock_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When forge provider is 'none', push/MR are skipped but report still publishes."""
    mock_config.workflow.forge.provider = "none"
    store = BatchStore(tmp_path / "batch_store.db")
    entry_id = store.add(_make_entry())
    entry = store.get_entry(entry_id)
    assert entry is not None
    entry.id = entry_id

    publish_called: list[bool] = []
    monkeypatch.setattr(
        "devflow.batch.publisher._publish_to_channels",
        lambda cfg, msg: publish_called.append(True) or "console",
    )
    fake_source = MagicMock()
    fake_source.update_task_status = MagicMock()
    fake_source.close = lambda: None
    monkeypatch.setattr(
        "devflow.batch.publisher.build_task_source", lambda wf: fake_source
    )

    publisher = BatchPublisher(mock_config, store, repo_path="/tmp/repo")
    result = publisher.publish(entry)

    # Report + tracker ran; status is published even without forge (no push/MR).
    assert publish_called == [True]
    assert result.status == BatchStatus.PUBLISHED
    assert result.mr_url is None
    store.close()
