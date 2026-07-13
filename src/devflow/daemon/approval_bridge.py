"""ApprovalBridge: connects LangGraph interrupt() to the ApprovalStore + push.

The bridge builds an ``ApprovalCallback`` (matching the type alias at
graph.py:264) that:
1. Registers the interrupt payload in the ``ApprovalStore``.
2. Sends push notifications ("approval pending") via configured channels.
3. Blocks on ``store.wait()`` with a timeout.
4. Returns the human decision dict, or a defer/reject decision on timeout.

The ``task_id`` from the interrupt payload is used as the store key
(thread_id), matching how the graph identifies runs.
"""

from __future__ import annotations

import logging
from typing import Any

from devflow.daemon.approval_store import ApprovalStore
from devflow.graph import ApprovalCallback
from devflow.notifications.base import NotificationChannel
from devflow.state import WorkflowState

logger = logging.getLogger(__name__)


class ApprovalBridge:
    """Bridges LangGraph interrupts to the ApprovalStore + push notifications."""

    def __init__(
        self,
        store: ApprovalStore,
        push_channels: list[NotificationChannel],
        approval_timeout_hours: int,
        on_timeout: str,
        review_url: str = "",
    ) -> None:
        self._store = store
        self._channels = push_channels
        self._timeout_seconds = approval_timeout_hours * 3600
        self._on_timeout = on_timeout
        self._review_url = review_url

    def build_callback(self) -> ApprovalCallback:
        """Return an ApprovalCallback for run_workflow_interactive."""

        def callback(payload: dict[str, Any], state: WorkflowState) -> dict[str, Any]:
            task_id = str(payload.get("task_id", "unknown"))
            gate = payload.get("gate_type", "approval")

            logger.info("ApprovalBridge: %s pending for task %s", gate, task_id)

            # 1. Register in store so the API can resolve it.
            self._store.register(task_id, payload)

            # 2. Send push notifications.
            self._send_push(gate, task_id, payload)

            # 3. Block on the store until resolved or timeout.
            decision = self._store.wait(task_id, timeout=self._timeout_seconds)

            if decision is not None:
                logger.info("ApprovalBridge: resolved for task %s", task_id)
                return decision

            # 4. Timeout: defer or reject.
            self._store.remove(task_id)
            logger.warning(
                "ApprovalBridge: timeout for task %s (policy=%s)",
                task_id,
                self._on_timeout,
            )
            reason = f"approval timeout (policy: {self._on_timeout})"
            return {
                "approved": False,
                "reason": reason,
                "requested_changes": [],
            }

        return callback

    def _send_push(self, gate: str, task_id: str, payload: dict[str, Any]) -> None:
        """Send a push notification about the pending approval. Best-effort."""
        title = payload.get("task_title", task_id)
        review_line = f"Review at: {self._review_url}\n" if self._review_url else ""
        message = (
            f"devflow: {gate} pending\n"
            f"Task: {title} ({task_id})\n"
            f"{review_line}"
        )
        if gate == "publish_approval":
            diff_preview = (payload.get("diff") or "")[:200]
            message += f"Diff preview:\n{diff_preview}\n"

        for channel in self._channels:
            try:
                channel.send(message)
            except Exception:
                logger.warning(
                    "Push channel '%s' failed for approval notification",
                    getattr(channel, "name", channel),
                    exc_info=True,
                )
