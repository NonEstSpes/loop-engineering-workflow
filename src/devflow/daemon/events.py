"""In-process pub/sub event bus for live workflow updates.

No external broker (Redis/etc) — the daemon is a single process, so an
in-memory asyncio queue per subscriber is sufficient. Phase 1 publishes
nothing; Phase 5 (Vue dashboard) consumes via SSE.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class EventBus:
    """Fan-out pub/sub: each subscriber gets its own asyncio.Queue.

    Topics are plain strings (e.g. ``"task.4321"``). Messages published
    before a subscription are not retained — this is a live stream, not a
    log.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}

    async def subscribe(self, topic: str) -> asyncio.Queue[dict[str, Any]]:
        """Subscribe to ``topic`` and return a queue to read messages from."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subscribers.setdefault(topic, []).append(queue)
        return queue

    async def publish(self, topic: str, data: dict[str, Any]) -> None:
        """Publish ``data`` to all subscribers of ``topic``.

        If a subscriber's queue is full, the message is dropped for that
        subscriber (logged) rather than blocking the publisher.
        """
        for queue in self._subscribers.get(topic, []):
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                logger.warning("EventBus queue full for topic '%s'; dropping message", topic)

    async def close(self) -> None:
        """Clear all subscribers. Queues are abandoned (callers should stop reading)."""
        self._subscribers.clear()
