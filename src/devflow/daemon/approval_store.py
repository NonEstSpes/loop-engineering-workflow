"""In-process store of pending human approvals.

When the LangGraph workflow hits an ``interrupt()`` (plan_approval or
publish_approval), the ApprovalBridge registers the interrupt payload here
and blocks on ``wait()``. The FastAPI ``POST /api/approvals/{thread_id}``
endpoint calls ``resolve()`` to deliver the human's decision, unblocking the
workflow.

Thread-safe: ``register``/``resolve``/``remove`` use a ``threading.Lock``;
``wait`` blocks on a per-thread ``threading.Event``.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


class _PendingApproval:
    """A single pending approval: payload + event + decision."""

    def __init__(self, thread_id: str, payload: dict[str, Any]) -> None:
        self.thread_id = thread_id
        self.payload = payload
        self.event = threading.Event()
        self.decision: dict[str, Any] | None = None
        # True once resolve() has delivered a decision. The entry is kept in
        # the dict (not deleted) so a wait() that starts AFTER resolve() can
        # still find the decision; get_pending() filters resolved entries out.
        self.resolved: bool = False


class ApprovalStore:
    """Thread-safe registry of pending human approvals."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, _PendingApproval] = {}

    def register(self, thread_id: str, payload: dict[str, Any]) -> None:
        """Register a new pending approval. Overwrites if thread_id exists."""
        with self._lock:
            self._pending[thread_id] = _PendingApproval(thread_id, payload)
        logger.info("Registered pending approval for thread %s", thread_id)

    def resolve(self, thread_id: str, decision: dict[str, Any]) -> bool:
        """Deliver a human decision to a pending approval.

        Returns True if the thread_id was found and resolved, False otherwise.

        Note: the entry is NOT removed from ``_pending``. Instead it is marked
        ``resolved=True`` so a ``wait()`` that starts after ``resolve()``
        (e.g. the bridge is still inside ``_send_push`` when the human
        approves) still finds the decision. ``get_pending()`` filters out
        resolved entries. Call ``remove()`` to actually delete the entry.
        """
        with self._lock:
            entry = self._pending.get(thread_id)
            if entry is None:
                logger.warning("resolve: unknown thread_id %s", thread_id)
                return False
            entry.decision = decision
            entry.resolved = True
            entry.event.set()
        approved = decision.get("approved")
        logger.info("Resolved approval for thread %s: approved=%s", thread_id, approved)
        return True

    def wait(self, thread_id: str, timeout: float) -> dict[str, Any] | None:
        """Block until a decision is available or timeout expires.

        Returns the decision dict, or None on timeout.
        """
        with self._lock:
            entry = self._pending.get(thread_id)
        if entry is None:
            logger.warning("wait: unknown thread_id %s", thread_id)
            return None
        if entry.event.wait(timeout=timeout):
            return entry.decision
        logger.warning("wait: timeout for thread %s after %ss", thread_id, timeout)
        return None

    def get_pending(self) -> list[dict[str, Any]]:
        """Return a list of pending approvals with their payloads.

        Each entry: ``{"thread_id": str, "payload": dict}``. Resolved entries
        (those that have already received a decision via ``resolve()``) are
        filtered out, even though they are still kept internally so a late
        ``wait()`` can still return the decision.
        """
        with self._lock:
            return [
                {"thread_id": e.thread_id, "payload": e.payload}
                for e in self._pending.values()
                if not e.resolved
            ]

    def is_resolved(self, thread_id: str) -> bool:
        """Return True if ``thread_id`` exists and has been resolved.

        Returns False if the thread_id is unknown or has not yet received a
        decision. Used by the bridge to distinguish a real timeout from an
        approval that was resolved while the bridge was busy.
        """
        with self._lock:
            entry = self._pending.get(thread_id)
            return entry is not None and entry.resolved

    def remove(self, thread_id: str) -> None:
        """Remove a pending approval (e.g. after timeout or cancellation)."""
        with self._lock:
            self._pending.pop(thread_id, None)
