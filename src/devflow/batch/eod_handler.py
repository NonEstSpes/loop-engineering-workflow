"""EodHandler: end-of-day batch review + publish orchestration.

Holds no locks itself — the caller (scheduler wrapper or API route) is
expected to hold ``DaemonLocks.eod_review()`` so task runs and EOD-publish
do not overlap.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from devflow.batch.models import BatchEntry
from devflow.batch.publisher import BatchPublisher
from devflow.batch.store import BatchStore
from devflow.config import Config
from devflow.daemon.events import EventBus

logger = logging.getLogger(__name__)


class EodHandler:
    """Coordinate EOD batch review and publish."""

    def __init__(
        self,
        app_cfg: Config,
        store: BatchStore,
        event_bus: EventBus,
        repo_path: str,
    ) -> None:
        self._cfg = app_cfg
        self._store = store
        self._bus = event_bus
        self._repo_path = repo_path

    def build_publisher(self) -> BatchPublisher:
        """Construct a BatchPublisher. Overridable in tests."""
        return BatchPublisher(self._cfg, self._store, self._repo_path)

    def list_pending(self) -> list[BatchEntry]:
        """Return all pending_review entries, oldest first."""
        return self._store.get_pending()

    def publish_selected(self, task_ids: list[str]) -> dict[str, list[str]]:
        """Publish entries whose task_id is in ``task_ids``.

        If ``task_ids`` is empty, ALL pending entries are published.
        Returns ``{"published": [...], "failed": [...], "skipped": [...]}``
        where each list holds task_ids. ``skipped`` covers pending entries
        not in ``task_ids`` AND task_ids that have no pending entry.
        """
        pending = self._store.get_pending()
        pending_ids = {e.task_id for e in pending}
        selected = set(task_ids) if task_ids else pending_ids

        publisher = self.build_publisher()
        published: list[str] = []
        failed: list[str] = []
        skipped: list[str] = []

        for entry in pending:
            if entry.task_id not in selected:
                skipped.append(entry.task_id)
                continue
            try:
                publisher.publish(entry)
                published.append(entry.task_id)
                logger.info("EOD: published task %s", entry.task_id)
            except Exception:
                logger.exception("EOD: publish failed for task %s", entry.task_id)
                failed.append(entry.task_id)

        # task_ids requested but with no pending entry.
        for tid in selected - pending_ids:
            skipped.append(tid)

        return {"published": published, "failed": failed, "skipped": skipped}

    def finalize(self) -> list[BatchEntry]:
        """Trigger EOD review: list pending + emit ``eod.ready`` event.

        Returns the pending entries. The ``eod.ready`` event is published
        best-effort on the ``eod`` topic (consumed by Phase 5 SSE / UI).
        """
        pending = self._store.get_pending()
        self._publish_event(
            "eod",
            {"event": "eod.ready", "pending_count": len(pending)},
        )
        logger.info("EOD finalize: %d pending entr(y/ies)", len(pending))
        return pending

    def _publish_event(self, topic: str, data: dict[str, Any]) -> None:
        """Best-effort event publish (mirrors WorkflowRunner._publish)."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._bus.publish(topic, data))
        except RuntimeError:
            try:
                asyncio.run(self._bus.publish(topic, data))
            except Exception as exc:
                logger.debug("EventBus publish failed for '%s': %s", topic, exc)
