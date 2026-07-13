"""SQLite-backed store for end-of-day batch entries.

The DB file lives at ``{repo_path}/.devflow/batch_store.db`` (the caller
chooses the path). Entries are serialized as JSON in a ``data`` column so
the full Pydantic model round-trips without column-per-field migrations.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from devflow.batch.models import BatchEntry, BatchStatus

logger = logging.getLogger(__name__)


class BatchStore:
    """CRUD for BatchEntry records in SQLite."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # ``check_same_thread=False`` because the store is shared across
        # threads: the daemon creates it on the main thread, but the FastAPI
        # request handlers (served by uvicorn/starlette's thread pool) and
        # APScheduler's job threads also call into it. SQLite's default
        # (``check_same_thread=True``) would raise ProgrammingError on the
        # first cross-thread use. The store is not thread-safe by default
        # (concurrent writes still need external locking, e.g. DaemonLocks),
        # but the daemon's access pattern is serialized through those locks.
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_schema()

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> BatchEntry:
        """Materialize a row into a BatchEntry, stamping the DB id onto it.

        The ``data`` JSON is the source of truth for fields, but ``id`` is a
        DB-assigned column — it is NOT stored in the JSON, so we set it from
        the row here. This lets callers (e.g. ``BatchPublisher``) rely on
        ``entry.id`` being populated after any read.
        """
        entry = BatchEntry.model_validate_json(row["data"])
        entry.id = row["id"]
        return entry

    def _create_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS batch_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending_review',
                created_at TEXT NOT NULL,
                data TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_batch_status ON batch_entries(status)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_batch_task ON batch_entries(task_id)"
        )
        self._conn.commit()

    def add(self, entry: BatchEntry) -> int:
        """Insert ``entry`` and return the assigned id.

        Also assigns the id back onto ``entry`` in memory so the caller can
        immediately use it (e.g. ``BatchPublisher.publish`` relies on
        ``entry.id`` to mark the row published).
        """
        cur = self._conn.execute(
            "INSERT INTO batch_entries (task_id, status, created_at, data) "
            "VALUES (?, ?, ?, ?)",
            (
                entry.task_id,
                entry.status,
                entry.created_at,
                entry.model_dump_json(),
            ),
        )
        self._conn.commit()
        assigned = cur.lastrowid
        assert assigned is not None  # AUTOINCREMENT always returns an id
        entry.id = assigned
        logger.debug("BatchStore: added entry id=%s for task %s", assigned, entry.task_id)
        return assigned

    def get_entry(self, entry_id: int) -> BatchEntry | None:
        """Return the entry with ``entry_id``, or None."""
        row = self._conn.execute(
            "SELECT id, data FROM batch_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_entry(row)

    def get_pending(self) -> list[BatchEntry]:
        """Return all pending_review entries, oldest first."""
        rows = self._conn.execute(
            "SELECT id, data FROM batch_entries WHERE status = ? ORDER BY id ASC",
            (BatchStatus.PENDING_REVIEW,),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def get_by_task(self, task_id: str) -> list[BatchEntry]:
        """Return all entries for ``task_id``, any status."""
        rows = self._conn.execute(
            "SELECT id, data FROM batch_entries WHERE task_id = ? ORDER BY id ASC",
            (task_id,),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def list_all(self, status: str | None = None) -> list[BatchEntry]:
        """Return all entries, optionally filtered by ``status``."""
        if status is None:
            rows = self._conn.execute(
                "SELECT id, data FROM batch_entries ORDER BY id ASC"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, data FROM batch_entries WHERE status = ? ORDER BY id ASC",
                (status,),
            ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def update_status(
        self,
        entry_id: int,
        status: str,
        *,
        mr_url: str | None = None,
        pushed_sha: str | None = None,
        rejection_reason: str | None = None,
    ) -> bool:
        """Update an entry's status (and optional publish/reject metadata).

        Returns True if a row was updated, False if the id was not found.
        The JSON ``data`` column is rewritten so the full entry reflects
        the new status + metadata (get_entry sees them without re-query).
        """
        row = self._conn.execute(
            "SELECT id, data FROM batch_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if row is None:
            return False

        entry = self._row_to_entry(row)
        entry.status = status
        if mr_url is not None:
            entry.mr_url = mr_url
        if pushed_sha is not None:
            entry.pushed_sha = pushed_sha
        if rejection_reason is not None:
            entry.rejection_reason = rejection_reason
        if status == BatchStatus.PUBLISHED:
            from datetime import UTC, datetime

            entry.published_at = datetime.now(UTC).isoformat()

        self._conn.execute(
            "UPDATE batch_entries SET status = ?, data = ? WHERE id = ?",
            (entry.status, entry.model_dump_json(), entry_id),
        )
        self._conn.commit()
        logger.debug("BatchStore: updated entry id=%s -> status=%s", entry_id, status)
        return True

    def count_pending(self) -> int:
        """Return the number of pending_review entries."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM batch_entries WHERE status = ?",
            (BatchStatus.PENDING_REVIEW,),
        ).fetchone()
        return int(row["n"])

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
