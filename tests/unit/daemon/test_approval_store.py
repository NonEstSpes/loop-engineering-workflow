"""Unit tests for the in-process ApprovalStore."""

from __future__ import annotations

import threading
import time

from devflow.daemon.approval_store import ApprovalStore


def test_register_and_get_pending() -> None:
    """A registered approval shows up in get_pending."""
    store = ApprovalStore()
    payload = {"gate_type": "plan_approval", "task_id": "T-1"}
    store.register("thread-1", payload)

    pending = store.get_pending()
    assert len(pending) == 1
    assert pending[0]["thread_id"] == "thread-1"
    assert pending[0]["payload"]["task_id"] == "T-1"


def test_resolve_unblocks_wait() -> None:
    """Resolving an approval unblocks a waiting thread."""
    store = ApprovalStore()
    store.register("thread-1", {"gate_type": "plan_approval", "task_id": "T-1"})

    result_holder: dict = {}

    def waiter() -> None:
        result_holder["decision"] = store.wait("thread-1", timeout=2.0)

    t = threading.Thread(target=waiter)
    t.start()

    # Give the waiter a moment to start blocking.
    time.sleep(0.05)

    decision = {"approved": True, "reason": "ok", "requested_changes": []}
    resolved = store.resolve("thread-1", decision)

    t.join(timeout=2.0)

    assert resolved is True
    assert result_holder["decision"] == decision


def test_wait_returns_none_on_timeout() -> None:
    """wait() returns None if no decision arrives within the timeout."""
    store = ApprovalStore()
    store.register("thread-1", {"gate_type": "plan_approval", "task_id": "T-1"})

    result = store.wait("thread-1", timeout=0.1)
    assert result is None
    # The approval is still pending after a timeout
    assert len(store.get_pending()) == 1


def test_resolve_unknown_thread_returns_false() -> None:
    """Resolving a thread that was never registered returns False."""
    store = ApprovalStore()
    decision = {"approved": True, "reason": "", "requested_changes": []}
    assert store.resolve("unknown", decision) is False


def test_remove_clears_entry() -> None:
    """remove() deletes a pending approval."""
    store = ApprovalStore()
    store.register("thread-1", {"task_id": "T-1"})
    store.remove("thread-1")
    assert store.get_pending() == []


def test_resolve_clears_entry() -> None:
    """After resolve, the entry is no longer pending."""
    store = ApprovalStore()
    store.register("thread-1", {"task_id": "T-1"})
    store.resolve("thread-1", {"approved": True, "reason": "", "requested_changes": []})
    assert store.get_pending() == []


def test_resolve_before_wait_returns_decision() -> None:
    """A resolve that happens BEFORE wait() must still return the decision (not None)."""
    store = ApprovalStore()
    store.register("T-1", {"task_id": "T-1"})
    # Resolve BEFORE calling wait (simulates fast human approval during push I/O)
    decision = {"approved": True, "reason": "fast", "requested_changes": []}
    store.resolve("T-1", decision)
    # Now wait — must return the decision, not None
    result = store.wait("T-1", timeout=1.0)
    assert result == decision


def test_is_resolved_reflects_state() -> None:
    """is_resolved() returns True after resolve, False before/unknown."""
    store = ApprovalStore()
    store.register("thread-1", {"task_id": "T-1"})
    assert store.is_resolved("thread-1") is False
    assert store.is_resolved("unknown") is False
    store.resolve("thread-1", {"approved": True, "reason": "", "requested_changes": []})
    assert store.is_resolved("thread-1") is True
    # Still pending-free in get_pending (filtered as resolved).
    assert store.get_pending() == []


# ---------------------------------------------------------------------------
# SSE publication (P2)
# ---------------------------------------------------------------------------

def test_register_publishes_approval_waiting() -> None:
    """register() publishes approval.waiting when event_bus is provided."""
    import asyncio
    from devflow.daemon.events import EventBus, GLOBAL_TOPIC
    from devflow.daemon.approval_store import ApprovalStore

    bus = EventBus()
    store = ApprovalStore(event_bus=bus)

    async def _check() -> dict:
        queue = await bus.subscribe(GLOBAL_TOPIC)
        store.register("thread-1", {"gate": "plan_approval", "task_id": "123"})
        return await asyncio.wait_for(queue.get(), timeout=2.0)

    result = asyncio.run(_check())
    assert result["event"] == "approval.waiting"
    assert result["thread_id"] == "thread-1"


def test_resolve_publishes_approval_resolved() -> None:
    """resolve() publishes approval.resolved when event_bus is provided."""
    import asyncio
    from devflow.daemon.events import EventBus, GLOBAL_TOPIC
    from devflow.daemon.approval_store import ApprovalStore

    bus = EventBus()
    store = ApprovalStore(event_bus=bus)
    store.register("thread-1", {"gate": "plan_approval"})

    async def _check() -> dict:
        queue = await bus.subscribe(GLOBAL_TOPIC)
        store.resolve("thread-1", {"approved": True, "reason": "looks good"})
        return await asyncio.wait_for(queue.get(), timeout=2.0)

    result = asyncio.run(_check())
    assert result["event"] == "approval.resolved"
    assert result["thread_id"] == "thread-1"
    assert result["approved"] is True


def test_no_event_bus_does_not_error() -> None:
    """register/resolve without event_bus work as before (no publication)."""
    from devflow.daemon.approval_store import ApprovalStore

    store = ApprovalStore()  # event_bus=None
    store.register("thread-1", {"gate": "plan_approval"})
    assert store.resolve("thread-1", {"approved": True}) is True
