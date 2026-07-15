"""SQLite-backed execution queue.

The queue is the LLM-evaluated execution order of tasks (separate from
TASKS.md which is the raw task list). The prioritizer node writes here;
humans reorder via the dashboard (drag-and-drop / up-down buttons).

The DB file lives at ``{repo_path}/.devflow/queue.db``. Thread-safe via a
``threading.Lock`` (same rationale as BatchStore: cross-thread access from
graph node + FastAPI handlers + APScheduler).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class QueueEntry(BaseModel):
    """A single entry in the execution queue."""

    position: int
    task_id: str
    task_title: str = ""
    priority: int | None = None
    reason: str = ""
    updated_at: str = ""


class QueueStore:
    """CRUD + reorder for the execution queue in SQLite."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._create_schema()

    def _create_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_queue (
                position INTEGER PRIMARY KEY,
                task_id TEXT NOT NULL UNIQUE,
                task_title TEXT DEFAULT '',
                priority INTEGER,
                reason TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            )
            """
        )
        self._conn.commit()

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> QueueEntry:
        return QueueEntry(
            position=row["position"],
            task_id=row["task_id"],
            task_title=row["task_title"] or "",
            priority=row["priority"],
            reason=row["reason"] or "",
            updated_at=row["updated_at"] or "",
        )

    def get_queue(self) -> list[QueueEntry]:
        """Return all entries ordered by position."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM execution_queue ORDER BY position"
            ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def set_queue(self, entries: list[QueueEntry]) -> None:
        """Overwrite the entire queue (used by the prioritizer node)."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute("DELETE FROM execution_queue")
            for i, e in enumerate(entries):
                self._conn.execute(
                    "INSERT INTO execution_queue (position, task_id, task_title, priority, reason, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (i, e.task_id, e.task_title, e.priority, e.reason, now),
                )
            self._conn.commit()

    def _rewrite_order(self, ordered_ids: list[str]) -> list[QueueEntry]:
        """Rewrite positions for the given task_id order. Caller holds lock."""
        now = datetime.now(timezone.utc).isoformat()
        rows = {
            r["task_id"]: r
            for r in self._conn.execute("SELECT * FROM execution_queue").fetchall()
        }
        self._conn.execute("DELETE FROM execution_queue")
        for i, tid in enumerate(ordered_ids):
            r = rows[tid]
            self._conn.execute(
                "INSERT INTO execution_queue (position, task_id, task_title, priority, reason, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (i, tid, r["task_title"], r["priority"], r["reason"], now),
            )
        self._conn.commit()
        # Re-read without lock (caller holds it) — fetch fresh rows.
        rows_out = self._conn.execute(
            "SELECT * FROM execution_queue ORDER BY position"
        ).fetchall()
        return [self._row_to_entry(r) for r in rows_out]

    def reorder(self, task_id: str, new_position: int) -> list[QueueEntry]:
        """Move task_id to new_position, shifting others. Returns updated queue."""
        with self._lock:
            current = [
                r["task_id"]
                for r in self._conn.execute(
                    "SELECT task_id FROM execution_queue ORDER BY position"
                ).fetchall()
            ]
            if task_id not in current:
                raise KeyError(f"Task {task_id} not in queue")
            if not (0 <= new_position < len(current)):
                raise ValueError(
                    f"new_position {new_position} out of range 0..{len(current) - 1}"
                )
            current.remove(task_id)
            current.insert(new_position, task_id)
            return self._rewrite_order(current)

    def move_up(self, task_id: str) -> list[QueueEntry]:
        """Swap task_id with the one above it. Noop if already first."""
        with self._lock:
            current = [
                r["task_id"]
                for r in self._conn.execute(
                    "SELECT task_id FROM execution_queue ORDER BY position"
                ).fetchall()
            ]
            if task_id not in current:
                raise KeyError(f"Task {task_id} not in queue")
            idx = current.index(task_id)
            if idx > 0:
                current[idx], current[idx - 1] = current[idx - 1], current[idx]
                return self._rewrite_order(current)
            rows = self._conn.execute(
                "SELECT * FROM execution_queue ORDER BY position"
            ).fetchall()
            return [self._row_to_entry(r) for r in rows]

    def move_down(self, task_id: str) -> list[QueueEntry]:
        """Swap task_id with the one below it. Noop if already last."""
        with self._lock:
            current = [
                r["task_id"]
                for r in self._conn.execute(
                    "SELECT task_id FROM execution_queue ORDER BY position"
                ).fetchall()
            ]
            if task_id not in current:
                raise KeyError(f"Task {task_id} not in queue")
            idx = current.index(task_id)
            if idx < len(current) - 1:
                current[idx], current[idx + 1] = current[idx + 1], current[idx]
                return self._rewrite_order(current)
            rows = self._conn.execute(
                "SELECT * FROM execution_queue ORDER BY position"
            ).fetchall()
            return [self._row_to_entry(r) for r in rows]

    def next_task_id(self) -> str | None:
        """Return the task_id at position 0, or None if queue is empty."""
        with self._lock:
            row = self._conn.execute(
                "SELECT task_id FROM execution_queue ORDER BY position LIMIT 1"
            ).fetchone()
        return row["task_id"] if row else None

    def remove(self, task_id: str) -> None:
        """Remove a task from the queue."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM execution_queue WHERE task_id = ?", (task_id,)
            )
            self._conn.commit()

    def clear(self) -> None:
        """Remove all entries."""
        with self._lock:
            self._conn.execute("DELETE FROM execution_queue")
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()
