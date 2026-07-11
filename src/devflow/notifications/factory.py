"""Factory for building NotificationChannel adapters from workflow configuration.

Mirrors :mod:`devflow.mcp.factory`: a registry mapping channel names to
``NotificationChannel`` subclasses, a ``build_notification_channels`` builder
that reads ``corporate_report_channels`` from the workflow config and env vars,
and a ``register_notification_channel`` hook for runtime extension.

The Telegram channel depends on the optional ``httpx`` package (the
``telegram`` extra). It is imported lazily inside the builder so the rest of
the system works without it; if httpx is missing, the ``telegram`` channel is
skipped with a warning rather than crashing.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from devflow.config import WorkflowConfig
from devflow.notifications.base import NotificationChannel
from devflow.notifications.console import ConsoleChannel

logger = logging.getLogger(__name__)

# Only the always-available channels are eagerly imported. Telegram is imported
# lazily because it pulls in the optional ``httpx`` dependency.
_NOTIFICATION_REGISTRY: dict[str, type[NotificationChannel]] = {
    "console": ConsoleChannel,
}

# Channels that are recognised in config but not yet implemented as real
# channels. They are skipped with a warning so existing configs keep working.
_STUB_CHANNELS = {"github", "gitlab", "slack", "teams"}

# Channels that require an optional dependency. They are imported lazily and
# skipped gracefully when the dependency is missing.
_OPTIONAL_CHANNELS = {"telegram"}


def _is_telegram_available() -> bool:
    """Return True when the optional ``httpx`` dependency is importable."""
    try:
        import httpx  # noqa: F401
    except ImportError:
        return False
    return True


def build_notification_channels(
    workflow_cfg: WorkflowConfig,
    extra: dict[str, Any] | None = None,
) -> list[NotificationChannel]:
    """Build a list of NotificationChannel from the workflow config.

    For each name in ``workflow_cfg.corporate_report_channels``:
    * a registered channel is built from env vars (+ optional ``extra``);
    * an optional channel (telegram) is imported lazily and skipped with a
      warning when its dependency (httpx) is not installed;
    * a known stub (github/gitlab/slack/teams) is skipped with a warning;
    * any other name raises ``ValueError``.

    Telegram reads ``TELEGRAM_BOT_TOKEN``, ``TELEGRAM_CHAT_ID`` and the optional
    ``TELEGRAM_API_BASE`` from the environment, mirroring the Redmine pattern.
    """
    extra = extra or {}
    channels: list[NotificationChannel] = []

    for name in workflow_cfg.corporate_report_channels:
        if name in _STUB_CHANNELS:
            logger.warning(
                "Notification channel '%s' is not yet implemented; skipping", name
            )
            continue

        # Optional dependency channels: import lazily, skip if unavailable.
        cls: type[NotificationChannel] | None = None
        if name in _OPTIONAL_CHANNELS:
            if name == "telegram" and not _is_telegram_available():
                logger.warning(
                    "Notification channel '%s' requires the optional 'httpx' "
                    "package; install it with `pip install -e '.[telegram]'`. "
                    "Skipping '%s'.",
                    name,
                    name,
                )
                continue
            # Lazy import so the module loads without httpx installed.
            from devflow.notifications.telegram import TelegramChannel

            cls = TelegramChannel
        else:
            cls = _NOTIFICATION_REGISTRY.get(name)
            if cls is None:
                raise ValueError(
                    f"Unknown notification channel '{name}'. "
                    f"Supported: {sorted(_NOTIFICATION_REGISTRY)} "
                    f"(+ optional: {sorted(_OPTIONAL_CHANNELS)}, "
                    f"stubs: {sorted(_STUB_CHANNELS)})"
                )

        if cls is None:
            continue

        if name == "telegram":
            config: dict[str, Any] = {
                "bot_token": os.getenv("TELEGRAM_BOT_TOKEN", extra.get("bot_token", "")),
                "chat_id": os.getenv("TELEGRAM_CHAT_ID", extra.get("chat_id", "")),
                "api_base": os.getenv(
                    "TELEGRAM_API_BASE", extra.get("api_base", "")
                ),
            }
        else:
            config = extra.get(name, {})

        channels.append(cls(config))

    return channels


def register_notification_channel(name: str, cls: type[NotificationChannel]) -> None:
    """Register a custom notification channel adapter."""
    if not issubclass(cls, NotificationChannel):
        raise TypeError(f"{cls} must be a subclass of NotificationChannel")
    _NOTIFICATION_REGISTRY[name] = cls
