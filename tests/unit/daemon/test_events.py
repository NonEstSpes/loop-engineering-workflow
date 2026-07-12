"""Unit tests for the in-process EventBus."""

from __future__ import annotations

import asyncio

import pytest

from devflow.daemon.events import EventBus


@pytest.mark.asyncio
async def test_publish_subscribe_single_topic() -> None:
    """A subscriber receives messages published to its topic."""
    bus = EventBus()
    queue = await bus.subscribe("task.4321")
    await bus.publish("task.4321", {"node": "maker", "status": "done"})
    msg = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert msg == {"node": "maker", "status": "done"}
    await bus.close()


@pytest.mark.asyncio
async def test_multiple_subscribers_same_topic() -> None:
    """Multiple subscribers each receive the same message."""
    bus = EventBus()
    q1 = await bus.subscribe("task.1")
    q2 = await bus.subscribe("task.1")
    await bus.publish("task.1", {"event": "started"})
    msg1 = await asyncio.wait_for(q1.get(), timeout=1.0)
    msg2 = await asyncio.wait_for(q2.get(), timeout=1.0)
    assert msg1 == {"event": "started"}
    assert msg2 == {"event": "started"}
    await bus.close()


@pytest.mark.asyncio
async def test_subscriber_does_not_receive_old_messages() -> None:
    """Messages published before subscription are not delivered."""
    bus = EventBus()
    await bus.publish("task.1", {"event": "old"})
    queue = await bus.subscribe("task.1")
    await bus.publish("task.1", {"event": "new"})
    msg = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert msg == {"event": "new"}
    await bus.close()


@pytest.mark.asyncio
async def test_close_unsubscribes_all() -> None:
    """After close, all queues are drained and closed."""
    bus = EventBus()
    queue = await bus.subscribe("task.1")
    await bus.close()
    assert queue.empty()
