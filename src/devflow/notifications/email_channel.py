"""Email notification channel via SMTP.

Sends notifications as plain-text emails. Uses STARTTLS by default.
Environment variables (read if not in config dict):
    SMTP_HOST     — SMTP server hostname
    SMTP_PORT     — SMTP port (default: 587)
    SMTP_USER     — SMTP username
    SMTP_PASSWORD — SMTP password
    EMAIL_FROM    — sender address
    EMAIL_TO      — recipient address
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Any

from devflow.notifications.base import NotificationChannel

logger = logging.getLogger(__name__)


class EmailChannel(NotificationChannel):
    """Send notifications via SMTP email."""

    name = "email"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._host = config.get("smtp_host") or os.getenv("SMTP_HOST", "")
        try:
            self._port = int(config.get("smtp_port", 0) or os.getenv("SMTP_PORT", "587"))
        except (ValueError, TypeError):
            self._port = 587
        self._user = config.get("smtp_user") or os.getenv("SMTP_USER", "")
        self._password = config.get("smtp_password") or os.getenv("SMTP_PASSWORD", "")
        self._from = config.get("from_addr") or os.getenv("EMAIL_FROM", "")
        self._to = config.get("to_addr") or os.getenv("EMAIL_TO", "")

    def send(self, message: str, *, parse_mode: str | None = None) -> str:
        """Send ``message`` as an email. Returns a mailto: identifier."""
        if not self._host or not self._from or not self._to:
            raise ValueError(
                "email channel requires smtp_host, from_addr, and to_addr "
                "(set SMTP_HOST, EMAIL_FROM, EMAIL_TO or config)"
            )

        msg = EmailMessage()
        msg["From"] = self._from
        msg["To"] = self._to
        msg["Subject"] = "devflow: approval pending"
        msg.set_content(message)

        with smtplib.SMTP(self._host, self._port, timeout=30) as server:
            server.starttls()
            if self._user and self._password:
                server.login(self._user, self._password)
            server.send_message(msg)

        logger.info("email: sent to %s", self._to)
        return f"mailto:{self._to}"

    def healthcheck(self) -> bool:
        """Return True when host, from, and to are all configured."""
        return bool(self._host and self._from and self._to)
