"""Batch publish: push branch, create MR, publish report, update tracker.

Sequential, idempotent. Each step is independent try/except — a failure
in one step does not abort the others. The entry is marked ``published``
in the store only when the publish completes (push/MR failures leave it
``pending_review`` so the next EOD retries — forge ops are idempotent).
"""

from __future__ import annotations

import contextlib
import logging

from devflow.batch.models import BatchEntry, BatchStatus
from devflow.batch.store import BatchStore
from devflow.config import Config
from devflow.forge.factory import build_forge_backend
from devflow.mcp.factory import build_task_source
from devflow.nodes.reporter import _build_report_markdown, _publish_to_channels
from devflow.state import Task

logger = logging.getLogger(__name__)


class BatchPublisher:
    """Publish a single BatchEntry: push, create MR, notify, update tracker."""

    def __init__(self, app_cfg: Config, store: BatchStore, repo_path: str) -> None:
        self._cfg = app_cfg
        self._store = store
        self._repo_path = repo_path

    def publish(self, entry: BatchEntry) -> BatchEntry:
        """Publish ``entry`` sequentially. Returns the (possibly updated) entry.

        Steps:
        1. forge.push(branch) -> pushed_sha (if forge configured)
        2. forge.create_mr(...) -> mr_url (if forge configured)
        3. _publish_to_channels(report) -> report_url
        4. source.update_task_status(resolved)
        5. mark entry PUBLISHED in store

        If push fails, the entry stays PENDING_REVIEW (retryable at next EOD).
        A create_mr failure is logged but does not block the published marking
        (the entry is published with mr_url=None).
        """
        forge_cfg = self._cfg.workflow.forge
        forge = None
        pushed_sha: str | None = None
        mr_url: str | None = None
        push_failed = False

        try:
            forge = build_forge_backend(self._cfg.workflow)
        except Exception as exc:
            logger.warning("Failed to build forge backend: %s", exc)

        # 1. Push (if forge configured).
        if forge is not None:
            try:
                pushed_sha = forge.push(
                    entry.branch_name, forge_cfg.target_branch, self._repo_path
                )
                logger.info(
                    "BatchPublish: pushed %s -> %s", entry.branch_name, pushed_sha[:8]
                )
            except Exception as exc:
                logger.warning(
                    "BatchPublish: push failed for %s: %s", entry.branch_name, exc
                )
                push_failed = True

            # 2. Create MR (only if push succeeded — MR without a push is pointless).
            if not push_failed:
                try:
                    mr_info = forge.create_mr(
                        branch=entry.branch_name,
                        target=forge_cfg.target_branch,
                        title=entry.reporter_artifacts.pr_title,
                        description=entry.reporter_artifacts.pr_description,
                    )
                    mr_url = mr_info.url
                    logger.info("BatchPublish: MR created %s", mr_url)
                except Exception as exc:
                    logger.warning(
                        "BatchPublish: create_mr failed for %s: %s",
                        entry.branch_name,
                        exc,
                    )

            with contextlib.suppress(Exception):  # pragma: no cover - defensive
                forge.close()

        # If push failed, keep the entry pending for retry.
        if push_failed:
            return entry

        # 3. Publish the report to notification channels (best-effort).
        try:
            report_text = _build_report_markdown(
                task=_entry_task_stub(entry),
                response=entry.reporter_artifacts,
                verdict=entry.final_verdict,
                reports=entry.checker_reports,
                branch=entry.branch_name,
            )
            _publish_to_channels(self._cfg, report_text)
        except Exception as exc:
            logger.warning(
                "BatchPublish: report publish failed for %s: %s", entry.task_id, exc
            )

        # 4. Update the tracker status (best-effort).
        try:
            source = build_task_source(self._cfg.workflow)
            try:
                verdict_str = (
                    entry.final_verdict.value if entry.final_verdict else "approve"
                )
                source.update_task_status(
                    entry.task_id,
                    "resolved",
                    comment=f"Final verdict: {verdict_str} (batch publish)",
                )
            finally:
                source.close()
        except Exception as exc:
            logger.warning(
                "BatchPublish: tracker update failed for %s: %s", entry.task_id, exc
            )

        # 5. Mark published in the store.
        if entry.id is not None:
            self._store.update_status(
                entry.id,
                BatchStatus.PUBLISHED,
                mr_url=mr_url,
                pushed_sha=pushed_sha,
            )

        entry.status = BatchStatus.PUBLISHED
        entry.pushed_sha = pushed_sha
        entry.mr_url = mr_url
        return entry


def _entry_task_stub(entry: BatchEntry) -> Task:
    """Build a minimal Task for _build_report_markdown.

    ``_build_report_markdown`` reads ``task.id`` and ``task.title`` — a
    BatchEntry doesn't carry the full Task model, so a tiny stub suffices.
    """
    return Task(id=entry.task_id, title=entry.task_title, description="")
