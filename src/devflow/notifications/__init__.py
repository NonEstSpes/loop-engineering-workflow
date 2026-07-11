"""Notification channel plug-in package.

Provides an ABC + factory + adapters pattern for publishing workflow
notifications (final reports, errors) to external channels such as the console
or Telegram. Mirrors the :mod:`devflow.mcp` task-source plug-in pattern.

Telegram is an optional integration: the :class:`TelegramChannel` adapter lives
in :mod:`devflow.notifications.telegram` and requires the ``httpx`` package
(install via the ``telegram`` extra). It is imported lazily by the factory and
by the CLI, so this package and the rest of the system work without it.
"""

from devflow.notifications.base import NotificationChannel
from devflow.notifications.console import ConsoleChannel
from devflow.notifications.factory import (
    build_notification_channels,
    register_notification_channel,
)

__all__ = [
    "NotificationChannel",
    "ConsoleChannel",
    "build_notification_channels",
    "register_notification_channel",
]
