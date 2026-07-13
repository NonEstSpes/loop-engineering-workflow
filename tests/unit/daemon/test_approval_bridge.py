"""Unit tests for the ApprovalBridge."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

from devflow.daemon.approval_bridge import ApprovalBridge
from devflow.daemon.approval_store import ApprovalStore


def _make_bridge(store: ApprovalStore | None = None) -> ApprovalBridge:
    """Build an ApprovalBridge with a mock push channel."""
    store = store or ApprovalStore()
    mock_channel = MagicMock()
    mock_channel.send.return_value = "ntfy://ok"
    return ApprovalBridge(
        store=store,
        push_channels=[mock_channel],
        approval_timeout_hours=1,
        on_timeout="defer",
        review_url="http://localhost:8787",
    )


def test_build_callback_returns_callable() -> None:
    """build_callback() returns a callable matching ApprovalCallback."""
    bridge = _make_bridge()
    callback = bridge.build_callback()
    assert callable(callback)


def test_callback_registers_and_sends_push() -> None:
    """The callback registers the payload in the store and sends a push."""
    store = ApprovalStore()
    bridge = _make_bridge(store=store)
    callback = bridge.build_callback()

    payload = {"gate_type": "plan_approval", "task_id": "T-1"}

    # Run the callback in a thread (it blocks on store.wait).
    result_holder: dict = {}

    def run_callback() -> None:
        result_holder["result"] = callback(payload, {"task": None})

    t = threading.Thread(target=run_callback)
    t.start()

    # Wait for the registration to appear.
    for _ in range(20):
        if store.get_pending():
            break
        time.sleep(0.05)

    assert len(store.get_pending()) == 1
    assert store.get_pending()[0]["payload"]["task_id"] == "T-1"

    # Resolve it.
    store.resolve("T-1", {"approved": True, "reason": "ok", "requested_changes": []})

    t.join(timeout=2.0)
    assert result_holder["result"]["approved"] is True


def test_callback_timeout_defer() -> None:
    """On timeout with on_timeout='defer', returns approved=False with reason."""
    store = ApprovalStore()
    bridge = ApprovalBridge(
        store=store,
        push_channels=[],
        approval_timeout_hours=0,  # timeout immediately
        on_timeout="defer",
    )
    callback = bridge.build_callback()

    payload = {"gate_type": "plan_approval", "task_id": "T-timeout"}
    result = callback(payload, {"task": None})

    assert result["approved"] is False
    assert "timeout" in result["reason"].lower()


def test_callback_timeout_reject() -> None:
    """On timeout with on_timeout='reject', returns approved=False with reject reason."""
    store = ApprovalStore()
    bridge = ApprovalBridge(
        store=store,
        push_channels=[],
        approval_timeout_hours=0,
        on_timeout="reject",
    )
    callback = bridge.build_callback()

    payload = {"gate_type": "plan_approval", "task_id": "T-reject"}
    result = callback(payload, {"task": None})

    assert result["approved"] is False
    assert "timeout" in result["reason"].lower()


def test_callback_thread_id_from_payload() -> None:
    """The callback uses task_id from the payload as the thread_id for the store."""
    store = ApprovalStore()
    bridge = _make_bridge(store=store)
    callback = bridge.build_callback()

    payload = {"gate_type": "publish_approval", "task_id": "T-42"}

    def run_and_resolve() -> None:
        # Wait for registration, then resolve.
        for _ in range(20):
            if store.get_pending():
                break
            time.sleep(0.05)
        store.resolve("T-42", {"approved": True, "reason": "", "requested_changes": []})

    resolver = threading.Thread(target=run_and_resolve)
    resolver.start()
    result = callback(payload, {"task": None})
    resolver.join(timeout=2.0)

    assert result["approved"] is True


def test_push_message_uses_review_url() -> None:
    """The push message contains the configured review_url (not hardcoded 8787)."""
    store = ApprovalStore()
    mock_channel = MagicMock()
    mock_channel.send.return_value = "ok"
    bridge = ApprovalBridge(
        store=store,
        push_channels=[mock_channel],
        approval_timeout_hours=1,
        on_timeout="defer",
        review_url="http://localhost:9999",
    )

    # Call _send_push directly to inspect the message.
    bridge._send_push("plan_approval", "T-1", {"task_title": "My Task"})

    sent_message = mock_channel.send.call_args[0][0]
    assert "http://localhost:9999" in sent_message
    assert "8787" not in sent_message
