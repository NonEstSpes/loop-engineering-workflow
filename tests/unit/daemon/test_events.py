"""Unit tests for the in-process EventBus."""

from __future__ import annotations

import asyncio

import pytest

from devflow.daemon.events import GLOBAL_TOPIC, EventBus


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


@pytest.mark.asyncio
async def test_global_topic_receives_all_events() -> None:
    """A subscriber on the global '*' topic receives every published event."""
    bus = EventBus()
    queue = await bus.subscribe(GLOBAL_TOPIC)

    await bus.publish("task.4321", {"event": "task.started", "task_id": "4321"})
    await bus.publish("eod", {"event": "eod.ready", "pending_count": 3})

    msg1 = await asyncio.wait_for(queue.get(), timeout=1.0)
    msg2 = await asyncio.wait_for(queue.get(), timeout=1.0)

    assert msg1["event"] == "task.started"
    assert msg2["event"] == "eod.ready"
    await bus.close()


@pytest.mark.asyncio
async def test_specific_topic_still_works_alongside_global() -> None:
    """A subscriber on a specific topic still gets only that topic's events."""
    bus = EventBus()
    specific_q = await bus.subscribe("task.1")
    global_q = await bus.subscribe(GLOBAL_TOPIC)

    await bus.publish("task.1", {"event": "task.started", "task_id": "1"})
    await bus.publish("task.2", {"event": "task.started", "task_id": "2"})

    specific_msg = await asyncio.wait_for(specific_q.get(), timeout=1.0)
    # specific_q should only have task.1's event
    assert specific_msg["task_id"] == "1"

    # global_q should have both
    g1 = await asyncio.wait_for(global_q.get(), timeout=1.0)
    g2 = await asyncio.wait_for(global_q.get(), timeout=1.0)
    assert {g1["task_id"], g2["task_id"]} == {"1", "2"}
    await bus.close()
