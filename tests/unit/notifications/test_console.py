"""Unit tests for the console notification channel."""

from __future__ import annotations

import logging

import pytest

from devflow.notifications.console import ConsoleChannel


def test_send_returns_console_url(caplog: pytest.LogCaptureFixture) -> None:
    channel = ConsoleChannel({})
    with caplog.at_level(logging.INFO, logger="devflow.notifications.console"):
        url = channel.send("my report")
    assert url == "console"
    assert "my report" in caplog.text


def test_send_ignores_parse_mode() -> None:
    channel = ConsoleChannel({})
    # parse_mode is accepted but does not change behaviour.
    assert channel.send("text", parse_mode="Markdown") == "console"


def test_healthcheck_true() -> None:
    assert ConsoleChannel({}).healthcheck() is True


def test_close_noop() -> None:
    # close() must not raise.
    ConsoleChannel({}).close()
