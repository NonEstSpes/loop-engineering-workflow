"""Abstract base for notification channels."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class NotificationChannel(ABC):
    """Adapter that publishes notifications to an external channel.

    Mirrors the :class:`devflow.mcp.base.TaskSource` plug-in pattern: a name, a
    config dict, an abstract ``send``, and optional ``close``/``healthcheck``.
    """

    name: str = "base"

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    @abstractmethod
    def send(self, message: str, *, parse_mode: str | None = None) -> str:
        """Publish ``message`` to the channel and return a channel-specific URL/id."""

    def close(self) -> None:  # noqa: B027 - optional hook
        """Release any resources (HTTP clients, connections)."""

    def healthcheck(self) -> bool:
        """Return True when the channel is ready to send."""
        return True
