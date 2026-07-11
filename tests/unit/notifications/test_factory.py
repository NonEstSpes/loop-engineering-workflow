"""Unit tests for the notification channel factory."""

from __future__ import annotations

import pytest

from devflow.config import WorkflowConfig
from devflow.notifications.console import ConsoleChannel
from devflow.notifications.factory import build_notification_channels
from devflow.notifications.telegram import TelegramChannel


def _workflow(channels: list[str]) -> WorkflowConfig:
    return WorkflowConfig(task_source="mock", corporate_report_channels=channels)


# ---------------------------------------------------------------------------
# console
# ---------------------------------------------------------------------------


def test_build_console_channel() -> None:
    channels = build_notification_channels(_workflow(["console"]))
    assert len(channels) == 1
    assert isinstance(channels[0], ConsoleChannel)


# ---------------------------------------------------------------------------
# telegram
# ---------------------------------------------------------------------------


def test_build_telegram_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    channels = build_notification_channels(_workflow(["telegram"]))
    assert len(channels) == 1
    assert isinstance(channels[0], TelegramChannel)
    assert channels[0].bot_token == "tok"
    assert channels[0].chat_id == "123"
    channels[0].close()


def test_build_telegram_missing_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises(ValueError, match="bot_token"):
        build_notification_channels(_workflow(["telegram"]))


def test_build_multiple_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    channels = build_notification_channels(_workflow(["console", "telegram"]))
    assert len(channels) == 2
    assert isinstance(channels[0], ConsoleChannel)
    assert isinstance(channels[1], TelegramChannel)
    channels[1].close()


# ---------------------------------------------------------------------------
# stub channels
# ---------------------------------------------------------------------------


def test_stub_channels_skipped(caplog: pytest.LogCaptureFixture) -> None:
    channels = build_notification_channels(_workflow(["github", "slack", "gitlab", "teams"]))
    assert channels == []
    messages = [r.getMessage() for r in caplog.records]
    assert any("github" in m and "not yet implemented" in m for m in messages)


# ---------------------------------------------------------------------------
# unknown channel
# ---------------------------------------------------------------------------


def test_unknown_channel_raises() -> None:
    with pytest.raises(ValueError, match="Unknown notification channel"):
        build_notification_channels(_workflow(["nonexistent"]))


def test_empty_channels() -> None:
    assert build_notification_channels(_workflow([])) == []


# ---------------------------------------------------------------------------
# telegram skipped when optional dependency missing
# ---------------------------------------------------------------------------


def test_telegram_skipped_when_httpx_missing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When httpx is not installed, the telegram channel is skipped gracefully."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(
        "devflow.notifications.factory._is_telegram_available", lambda: False
    )
    with caplog.at_level("WARNING", logger="devflow.notifications.factory"):
        channels = build_notification_channels(_workflow(["telegram"]))
    assert channels == []
    assert any("httpx" in r.getMessage() and "telegram" in r.getMessage() for r in caplog.records)


def test_telegram_built_when_httpx_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """When httpx is installed, the telegram channel is built normally."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(
        "devflow.notifications.factory._is_telegram_available", lambda: True
    )
    channels = build_notification_channels(_workflow(["telegram"]))
    assert len(channels) == 1
    assert channels[0].name == "telegram"
    channels[0].close()
