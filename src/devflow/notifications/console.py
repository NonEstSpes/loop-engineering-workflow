"""Console notification channel: logs the message via the module logger."""

from __future__ import annotations

import logging
from typing import Any

from devflow.notifications.base import NotificationChannel

logger = logging.getLogger(__name__)


class ConsoleChannel(NotificationChannel):
    """Log the message to the standard logger. Always succeeds."""

    name = "console"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config or {})

    def send(self, message: str, *, parse_mode: str | None = None) -> str:
        logger.info("Report:\n%s", message)
        return "console"
