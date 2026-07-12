"""ntfy.sh notification channel.

Sends push notifications via the ntfy protocol (HTTP POST to a topic).
Works with the public ntfy.sh server or a self-hosted instance. Supports
optional authentication via a bearer token (for self-hosted with auth).

``httpx`` is an optional dependency: install it via the ``web`` extra
(``pip install -e '.[web]'``). If it is not installed, importing this
module still succeeds but :class:`NtfyChannel` raises a clear
:class:`RuntimeError` at construction time so the system keeps working
without ntfy. The factory skips the ``ntfy`` channel when httpx is missing.

Environment variables (read if not in config dict):
    NTFY_SERVER  - base URL (default: https://ntfy.sh)
    NTFY_TOPIC   - topic to publish to
    NTFY_TOKEN   - optional bearer token for auth
"""

from __future__ import annotations

import logging
import os
from typing import Any

from devflow.notifications.base import NotificationChannel

logger = logging.getLogger(__name__)

try:
    import httpx
except ImportError:  # pragma: no cover - optional dependency
    httpx = None  # type: ignore[assignment]

DEFAULT_SERVER = "https://ntfy.sh"
DEFAULT_TIMEOUT = 10.0


class NtfyChannel(NotificationChannel):
    """Push notifications via ntfy.sh or a self-hosted ntfy server."""

    name = "ntfy"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        if httpx is None:
            raise RuntimeError(
                "ntfy channel requires the 'httpx' package. "
                "Install it with: pip install -e '.[web]'"
            )
        self._server = (
            config.get("server") or os.getenv("NTFY_SERVER", DEFAULT_SERVER)
        ).rstrip("/")
        self._topic = config.get("topic") or os.getenv("NTFY_TOPIC", "")
        self._token = config.get("token") or os.getenv("NTFY_TOKEN", "")
        self._client: httpx.Client | None = None

    def send(self, message: str, *, parse_mode: str | None = None) -> str:
        """POST ``message`` to the ntfy topic. Returns the topic URL.

        ``parse_mode`` is accepted for interface compatibility with other
        channels but ntfy does not use a markdown/HTML mode for the body;
        the message is sent verbatim.
        """
        if not self._topic:
            raise ValueError(
                "ntfy channel requires a 'topic' (set NTFY_TOPIC or config)"
            )

        url = f"{self._server}/{self._topic}"
        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        # Use a fresh short-lived client per send. The daemon publishes
        # infrequently and this avoids managing a long-lived connection.
        # trust_env=True honours HTTP_PROXY/HTTPS_PROXY/SSL_CERT_FILE, matching
        # the project's network configuration policy (see TelegramChannel).
        with httpx.Client(timeout=DEFAULT_TIMEOUT, trust_env=True) as client:
            resp = client.post(url, content=message, headers=headers)
            resp.raise_for_status()

        logger.info("ntfy: published to topic '%s'", self._topic)
        return url

    def healthcheck(self) -> bool:
        """Return True when a topic is configured."""
        return bool(self._topic)
