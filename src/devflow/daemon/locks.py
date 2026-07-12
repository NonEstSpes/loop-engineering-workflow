"""Concurrency locks for the daemon.

Two logical operations must not run in parallel within the daemon:
- ``task_run``: an active workflow run (processing tasks).
- ``eod_review``: an end-of-day batch review / publish.

Each is an ``asyncio.Lock``. Callers use ``async with locks.task_run():``
to acquire. The locks are independent — task_run does not block eod_review
and vice versa. Higher-level orchestration (Phase 4) coordinates ordering.
"""

from __future__ import annotations

import asyncio


class DaemonLocks:
    """Holds asyncio locks for daemon-wide exclusive operations."""

    def __init__(self) -> None:
        self._task_run = asyncio.Lock()
        self._eod_review = asyncio.Lock()

    def task_run(self) -> asyncio.Lock:
        """Lock for an active workflow task run. Only one at a time."""
        return self._task_run

    def eod_review(self) -> asyncio.Lock:
        """Lock for an EOD batch review/publish. Only one at a time."""
        return self._eod_review
