"""SQLite-backed event history store.

Subscribes to the EventBus (via a background task in the daemon) and persists
every event to SQLite. The dashboard reads the history via
``GET /api/events/history`` for the Activity Log view.

The DB file lives at ``{repo_path}/.devflow/events.db``. Thread-safe via a
``threading.Lock`` (cross-thread access from daemon task + FastAPI handlers).
Auto-prunes to ``MAX_EVENTS`` (1000) to bound growth.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class EventLogEntry(BaseModel):
    """A single persisted event in the history log."""

    id: int
    timestamp: str  # ISO 8601 UTC
    event_type: str
    data: dict[str, Any]


class EventStore:
    """CRUD for event history in SQLite with auto-pruning."""

    _MAX_EVENTS = 1000

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
            CREATE TABLE IF NOT EXISTS event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                data TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def add(self, event_type: str, data: dict[str, object]) -> None:
        """Insert an event. Auto-prunes oldest entries over _MAX_EVENTS."""
        now = datetime.now(timezone.utc).isoformat()
        serialized = json.dumps(data, default=str, ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                "INSERT INTO event_log (timestamp, event_type, data) VALUES (?, ?, ?)",
                (now, event_type, serialized),
            )
            self._conn.commit()
            self._prune()

    def _prune(self) -> None:
        """Delete oldest entries beyond _MAX_EVENTS. Caller holds lock."""
        count_row = self._conn.execute("SELECT COUNT(*) as c FROM event_log").fetchone()
        count = count_row["c"] if count_row else 0
        if count > self._MAX_EVENTS:
            excess = count - self._MAX_EVENTS
            self._conn.execute(
                "DELETE FROM event_log WHERE id IN "
                "(SELECT id FROM event_log ORDER BY id ASC LIMIT ?)",
                (excess,),
            )
            self._conn.commit()
            logger.debug("Pruned %d old events from event_log", excess)

    def get_recent(
        self,
        limit: int = 100,
        event_type: str | None = None,
    ) -> list[EventLogEntry]:
        """Return recent events (newest first), optionally filtered by type prefix."""
        with self._lock:
            if event_type is not None:
                rows = self._conn.execute(
                    "SELECT * FROM event_log WHERE event_type LIKE ? ORDER BY id DESC LIMIT ?",
                    (f"{event_type}%", limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM event_log ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [
            EventLogEntry(
                id=row["id"],
                timestamp=row["timestamp"],
                event_type=row["event_type"],
                data=json.loads(row["data"]),
            )
            for row in rows
        ]

    def close(self) -> None:
        self._conn.close()
