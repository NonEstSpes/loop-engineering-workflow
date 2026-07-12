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
        """
        with self._lock:
            entry = self._pending.get(thread_id)
            if entry is None:
                logger.warning("resolve: unknown thread_id %s", thread_id)
                return False
            entry.decision = decision
            entry.event.set()
            # Remove from pending immediately so get_pending() reflects the change.
            del self._pending[thread_id]
        logger.info("Resolved approval for thread %s: approved=%s", thread_id, decision.get("approved"))
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

        Each entry: ``{"thread_id": str, "payload": dict}``.
        """
        with self._lock:
            return [
                {"thread_id": e.thread_id, "payload": e.payload}
                for e in self._pending.values()
            ]

    def remove(self, thread_id: str) -> None:
        """Remove a pending approval (e.g. after timeout or cancellation)."""
        with self._lock:
            self._pending.pop(thread_id, None)
