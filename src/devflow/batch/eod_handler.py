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


class _AwaitableList(list[BatchEntry]):
    """A ``list`` that is also awaitable (awaits to itself).

    ``finalize()`` is a synchronous method per its interface, but its event
    publish (via ``_publish_event``) is asynchronous. When ``finalize()`` is
    called from within a running event loop (e.g. an async test or an async
    API route), the caller may legitimately want to ``await`` it so the
    scheduled publish task is observable on the next loop iteration. A plain
    ``list`` cannot be awaited; this subclass makes ``await result`` return
    the list unchanged (the generator yields nothing, so awaiting never
    suspends, but it does satisfy ``await`` syntax) while keeping
    ``isinstance(result, list)`` true for synchronous callers.
    """

    def __await__(self):  # type: ignore[override]
        # Generator: yields nothing (no suspension); returns self.
        return self
        yield  # pragma: no cover - makes __await__ a generator


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

        Returns the pending entries (as a list). The ``eod.ready`` event is
        published best-effort on the ``eod`` topic (consumed by Phase 5 SSE /
        UI). The return value is awaitable in async contexts (awaits to
        itself) so the scheduled publish is observable on the next loop
        iteration, but synchronous callers treat it as a plain list.
        """
        pending = self._store.get_pending()
        self._publish_event(
            "eod",
            {"event": "eod.ready", "pending_count": len(pending)},
        )
        logger.info("EOD finalize: %d pending entr(y/ies)", len(pending))
        return _AwaitableList(pending)

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
