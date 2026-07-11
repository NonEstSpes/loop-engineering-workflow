"""Telegram human-in-the-loop bridge.

Bridges LangGraph's ``interrupt()`` pause in :func:`devflow.nodes.plan_approval`
with an interactive Telegram chat: sends the plan to the configured chat with
inline buttons (approve / reject / request changes), waits for the human's
choice, optionally collects a free-form follow-up (reason or requested changes),
and returns the resume value expected by the plan-approval node.

The returned dict matches the shape documented in ``docs/architecture.md``::

    {"approved": bool, "reason": str, "requested_changes": list[str]}
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from devflow.schemas import Plan
from devflow.state import Task

if TYPE_CHECKING:
    # Imported only for type hints; avoids pulling in the optional httpx
    # dependency at runtime when Telegram is not used.
    from devflow.notifications.telegram import TelegramChannel

logger = logging.getLogger(__name__)

# Callback data strings encoded in the inline keyboard buttons.
CB_APPROVE = "approve"
CB_REJECT = "reject"
CB_CHANGES = "changes"

_DEFAULT_TIMEOUT = 300.0


class TelegramBridge:
    """Run human-in-the-loop plan approval flows via a :class:`TelegramChannel`."""

    def __init__(self, channel: TelegramChannel, *, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self.channel = channel
        self.timeout = timeout

    def request_plan_approval(self, task: Task, plan: Plan) -> dict[str, Any]:
        """Ask the human to approve ``plan`` for ``task`` via Telegram.

        Returns a resume-value dict ``{"approved": bool, "reason": str,
        "requested_changes": list[str]}``. Raises :class:`TimeoutError` when the
        human does not respond within the configured timeout.
        """
        message = _format_plan_message(task, plan)
        buttons = [
            [{"text": "✅ Одобрить", "callback_data": CB_APPROVE}],
            [{"text": "❌ Отклонить", "callback_data": CB_REJECT}],
            [{"text": "✏️ Запросить изменения", "callback_data": CB_CHANGES}],
        ]
        message_id = self.channel.send_with_inline_keyboard(message, buttons)
        decision = self.channel.wait_for_callback_query(message_id, timeout=self.timeout)
        logger.info("Telegram plan-approval decision for task %s: %s", task.id, decision)

        if decision == CB_APPROVE:
            return {
                "approved": True,
                "reason": "Approved via Telegram",
                "requested_changes": [],
            }

        if decision == CB_REJECT:
            self.channel.send("Опишите причину отклонения:")
            reason = self.channel.wait_for_text_reply(timeout=self.timeout)
            return {
                "approved": False,
                "reason": reason,
                "requested_changes": [],
            }

        if decision == CB_CHANGES:
            self.channel.send("Опишите запрошенные изменения (каждый пункт с новой строки):")
            text = self.channel.wait_for_text_reply(timeout=self.timeout)
            requested_changes = [
                line.lstrip("- *•").strip()
                for line in text.splitlines()
                if line.strip()
            ]
            return {
                "approved": False,
                "reason": "Changes requested via Telegram",
                "requested_changes": requested_changes,
            }

        # Unexpected callback data — treat as rejection with an explanation.
        logger.warning("Unknown Telegram approval callback: %s", decision)
        return {
            "approved": False,
            "reason": f"Unknown decision: {decision}",
            "requested_changes": [],
        }


def _format_plan_message(task: Task, plan: Plan) -> str:
    """Render a Markdown plan-approval prompt for Telegram."""
    lines = [
        f"*📋 План на одобрение: {task.title}*",
        "",
        f"Задача: `{task.id}`",
        "",
        f"{plan.summary}",
        "",
        "*Шаги:*",
    ]
    for step in plan.steps:
        lines.append(f"• `{step.id}` — {step.description}")
        if step.files_to_touch:
            lines.append(f"  файлы: {', '.join(step.files_to_touch)}")
        if step.estimated_risk and step.estimated_risk != "low":
            lines.append(f"  риск: {step.estimated_risk}")
    if plan.notes:
        lines.append("")
        lines.append(f"_{plan.notes}_")
    lines.append("")
    lines.append("Выберите действие:")
    return "\n".join(lines)
