"""Tests for QueueStore — SQLite-backed execution queue."""

from __future__ import annotations

from pathlib import Path

import pytest

from devflow.batch.queue_store import QueueEntry, QueueStore


def _make_store(tmp_path: Path) -> QueueStore:
    return QueueStore(str(tmp_path / "queue.db"))


def _entry(task_id: str, title: str = "Task", priority: int | None = 3) -> QueueEntry:
    return QueueEntry(
        position=0,
        task_id=task_id,
        task_title=title,
        priority=priority,
        reason="",
        updated_at="2026-01-01T00:00:00",
    )


def test_set_and_get_queue_round_trip(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    entries = [
        _entry("1", "First", 0),
        _entry("2", "Second", 1),
    ]
    store.set_queue(entries)
    result = store.get_queue()
    assert len(result) == 2
    assert result[0].task_id == "1"
    assert result[0].position == 0
    assert result[1].task_id == "2"
    assert result[1].position == 1


def test_set_queue_overwrites_previous(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.set_queue([_entry("1"), _entry("2"), _entry("3")])
    store.set_queue([_entry("4"), _entry("5")])
    result = store.get_queue()
    assert len(result) == 2
    assert result[0].task_id == "4"


def test_reorder_moves_task_to_new_position(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.set_queue([_entry("1"), _entry("2"), _entry("3")])
    # Move task "3" to position 0.
    result = store.reorder("3", 0)
    assert [e.task_id for e in result] == ["3", "1", "2"]
    # Positions are sequential 0..N-1.
    assert [e.position for e in result] == [0, 1, 2]


def test_move_up(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.set_queue([_entry("1"), _entry("2"), _entry("3")])
    result = store.move_up("3")
    assert [e.task_id for e in result] == ["1", "3", "2"]


def test_move_up_first_is_noop(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.set_queue([_entry("1"), _entry("2")])
    result = store.move_up("1")
    assert [e.task_id for e in result] == ["1", "2"]


def test_move_down(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.set_queue([_entry("1"), _entry("2"), _entry("3")])
    result = store.move_down("1")
    assert [e.task_id for e in result] == ["2", "1", "3"]


def test_move_down_last_is_noop(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.set_queue([_entry("1"), _entry("2")])
    result = store.move_down("2")
    assert [e.task_id for e in result] == ["1", "2"]


def test_next_task_id_returns_first(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.set_queue([_entry("1"), _entry("2")])
    assert store.next_task_id() == "1"


def test_next_task_id_empty_returns_none(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    assert store.next_task_id() is None


def test_remove_task(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.set_queue([_entry("1"), _entry("2"), _entry("3")])
    store.remove("2")
    result = store.get_queue()
    assert [e.task_id for e in result] == ["1", "3"]


def test_reorder_unknown_task_raises(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.set_queue([_entry("1")])
    with pytest.raises(KeyError):
        store.reorder("bogus", 0)
