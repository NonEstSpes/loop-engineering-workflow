"""Tests for todo_api helpers (web-facing TODO read/rewrite)."""

from __future__ import annotations

from pathlib import Path

from devflow.daemon.todo_api import rewrite_todo_line, serialize_todo
from devflow.todo import parse_todo

_TODO_CONTENT = """\
# TODO
- [ ] #r2 [#251977](https://example.com/251977) — Fix the bug
- [ ] #r1 — Urgent fix
- [~] #r3 — In progress task
- [x] #r4 — Done task
"""


def _write_todo(tmp_path: Path) -> Path:
    path = tmp_path / "TODO.md"
    path.write_text(_TODO_CONTENT, encoding="utf-8")
    return path


def test_serialize_todo_returns_json_entries(tmp_path: Path) -> None:
    path = _write_todo(tmp_path)
    items = parse_todo(path)
    data = serialize_todo(items)
    # First line is a heading (non-task), second is the task with priority 2.
    assert data[0]["line_no"] == 1
    assert data[0]["checkbox"] is None
    assert data[1]["line_no"] == 2
    assert data[1]["checkbox"] == "[ ]"
    assert data[1]["priority"] == 2
    assert data[1]["task_ref"] == "251977"


def test_rewrite_todo_line_changes_priority(tmp_path: Path) -> None:
    path = _write_todo(tmp_path)
    result = rewrite_todo_line(path, line_no=2, priority=0)
    assert result["priority"] == 0
    # Re-read from disk to confirm persistence.
    items = parse_todo(path)
    assert items[1].priority == 0


def test_rewrite_todo_line_changes_status(tmp_path: Path) -> None:
    path = _write_todo(tmp_path)
    result = rewrite_todo_line(path, line_no=2, status="in_progress")
    assert result["checkbox"] == "[~]"
    items = parse_todo(path)
    assert items[1].checkbox == "[~]"


def test_rewrite_todo_line_missing_line_raises(tmp_path: Path) -> None:
    import pytest

    path = _write_todo(tmp_path)
    with pytest.raises(ValueError, match="not found"):
        rewrite_todo_line(path, line_no=999, priority=0)


def test_rewrite_todo_line_invalid_priority_raises(tmp_path: Path) -> None:
    import pytest

    path = _write_todo(tmp_path)
    with pytest.raises(ValueError, match="priority"):
        rewrite_todo_line(path, line_no=2, priority=7)


def test_rewrite_todo_line_non_task_line_raises(tmp_path: Path) -> None:
    import pytest

    path = _write_todo(tmp_path)
    # Line 1 is the heading "# TODO" — not a task.
    with pytest.raises(ValueError, match="not a task"):
        rewrite_todo_line(path, line_no=1, priority=0)
