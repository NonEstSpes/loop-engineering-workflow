"""Web-facing helpers for reading and rewriting TODO.md entries.

Used by the ``/api/todo`` endpoints. The orchestrator re-reads TODO.md from
disk on every run, so editing the file is sufficient for changes to take
effect — no in-memory daemon state needs invalidation.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

from devflow.state import (
    CHECKBOX_DONE,
    CHECKBOX_IN_PROGRESS,
    CHECKBOX_OPEN,
    TodoItem,
)
from devflow.todo import PRIORITY_RE, parse_todo

# Maps the web-facing status string to the checkbox marker.
_STATUS_TO_CHECKBOX: dict[str, str] = {
    "open": CHECKBOX_OPEN,
    "in_progress": CHECKBOX_IN_PROGRESS,
    "done": CHECKBOX_DONE,
}


def serialize_todo(items: list[TodoItem]) -> list[dict[str, Any]]:
    """Serialize parsed TODO items into JSON-friendly dicts."""
    return [
        {
            "line_no": item.line_no,
            "text": item.raw_line,
            "checkbox": item.checkbox,
            "priority": item.priority,
            "task_ref": item.task_ref,
            "url": item.url,
            "title": item.title,
        }
        for item in items
    ]


def _replace_priority(raw: str, new_priority: int) -> str:
    """Swap or insert a #rX priority tag in a raw line."""
    if PRIORITY_RE.search(raw):
        return PRIORITY_RE.sub(f"#r{new_priority}", raw, count=1)
    # No existing tag: insert after the checkbox marker.
    return re.sub(r"^(\s*[-*]\s+\[[ |~x]\])", rf"\1 #r{new_priority}", raw, count=1)


def _replace_checkbox(raw: str, new_checkbox: str) -> str:
    """Swap the checkbox marker in a raw line."""
    for marker in (CHECKBOX_OPEN, CHECKBOX_IN_PROGRESS, CHECKBOX_DONE):
        if marker in raw:
            return raw.replace(marker, new_checkbox, 1)
    return raw


def _atomic_write(path: Path, content: str) -> None:
    """Write content atomically: temp file in same dir + os.replace."""
    dir_ = path.parent
    fd, tmp_name = tempfile.mkstemp(dir=str(dir_), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_name, path)
    except Exception:
        # Clean up the temp file on failure.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def rewrite_todo_line(
    path: Path,
    line_no: int,
    *,
    priority: int | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Atomically rewrite a single TODO.md line's priority and/or status.

    Returns the updated serialized entry. Raises ``ValueError`` if the line
    does not exist, is not a task line, or the priority/status is invalid.
    """
    if priority is not None and not (0 <= priority <= 5):
        raise ValueError(f"Invalid priority {priority}: must be 0..5")
    if status is not None and status not in _STATUS_TO_CHECKBOX:
        raise ValueError(f"Invalid status {status!r}")

    items = parse_todo(path)
    target = next((it for it in items if it.line_no == line_no), None)
    if target is None:
        raise ValueError(f"TODO line {line_no} not found")
    if not target.is_task:
        raise ValueError(f"TODO line {line_no} is not a task line")

    new_line = target.raw_line
    if priority is not None:
        new_line = _replace_priority(new_line, priority)
    if status is not None:
        new_line = _replace_checkbox(new_line, _STATUS_TO_CHECKBOX[status])

    # Rewrite the whole file with the single updated line (atomic).
    lines = path.read_text(encoding="utf-8").splitlines()
    idx = line_no - 1
    if 0 <= idx < len(lines):
        lines[idx] = new_line
    _atomic_write(path, "\n".join(lines) + "\n")

    # Re-parse to return the updated entry with fresh fields.
    updated = parse_todo(path)
    return serialize_todo([it for it in updated if it.line_no == line_no])[0]
