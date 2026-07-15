"""Tests for EventStore — SQLite-backed event history."""

from __future__ import annotations

from pathlib import Path

from devflow.batch.event_store import EventLogEntry, EventStore


def _make_store(tmp_path: Path) -> EventStore:
    return EventStore(str(tmp_path / "events.db"))


def test_add_and_get_recent_round_trip(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.add("task.started", {"task_id": "1", "event": "task.started"})
    store.add("task.finished", {"task_id": "1", "event": "task.finished"})
    result = store.get_recent(limit=10)
    assert len(result) == 2
    # Newest first (highest id first).
    assert result[0].event_type == "task.finished"
    assert result[1].event_type == "task.started"


def test_get_recent_with_limit(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    for i in range(5):
        store.add("task.started", {"i": i})
    result = store.get_recent(limit=3)
    assert len(result) == 3
    # Newest first: i=4, i=3, i=2.
    assert result[0].data["i"] == 4
    assert result[2].data["i"] == 2


def test_get_recent_filter_by_event_type(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.add("task.started", {"event": "task.started"})
    store.add("approval.waiting", {"event": "approval.waiting"})
    store.add("queue.updated", {"event": "queue.updated"})
    # Filter "approval" → prefix match.
    result = store.get_recent(limit=10, event_type="approval")
    assert len(result) == 1
    assert result[0].event_type == "approval.waiting"


def test_get_recent_filter_prefix_match(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.add("task.started", {})
    store.add("task.finished", {})
    store.add("approval.waiting", {})
    # "task" → matches task.started + task.finished.
    result = store.get_recent(limit=10, event_type="task")
    assert len(result) == 2
    assert all(r.event_type.startswith("task") for r in result)


def test_auto_prune_when_over_max(tmp_path: Path) -> None:
    """EventStore prunes to MAX_EVENTS (1000) oldest entries."""
    store = _make_store(tmp_path)
    # Override for a faster test.
    store._MAX_EVENTS = 5
    for i in range(10):
        store.add("task.started", {"i": i})
    result = store.get_recent(limit=100)
    assert len(result) == 5
    # Oldest 5 pruned; remaining are i=5..9.
    ids = sorted(r.data["i"] for r in result)
    assert ids == [5, 6, 7, 8, 9]


def test_empty_store_returns_empty_list(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    assert store.get_recent(limit=10) == []
