"""Unit tests for the email notification channel."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from devflow.notifications.email_channel import EmailChannel


def test_email_channel_name() -> None:
    """The channel name is 'email'."""
    channel = EmailChannel({
        "smtp_host": "smtp.example.com",
        "from_addr": "bot@corp.com",
        "to_addr": "dev@corp.com",
    })
    assert channel.name == "email"


def test_email_send_uses_smtp() -> None:
    """send() connects to SMTP and sends the message."""
    channel = EmailChannel({
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_user": "bot",
        "smtp_password": "pass",
        "from_addr": "bot@corp.com",
        "to_addr": "dev@corp.com",
    })

    with patch("devflow.notifications.email_channel.smtplib.SMTP") as mock_smtp:
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_server

        result = channel.send("Task T-1 approved", parse_mode=None)

    mock_smtp.assert_called_once_with("smtp.example.com", 587, timeout=30)
    mock_server.starttls.assert_called_once()
    mock_server.login.assert_called_once_with("bot", "pass")
    mock_server.send_message.assert_called_once()
    assert "dev@corp.com" in result


def test_email_healthcheck_without_host_returns_false() -> None:
    """healthcheck() returns False when smtp_host is missing."""
    channel = EmailChannel({"from_addr": "a@b.com", "to_addr": "c@d.com"})
    assert channel.healthcheck() is False


def test_email_healthcheck_with_config_returns_true() -> None:
    """healthcheck() returns True when smtp_host, from, and to are set."""
    channel = EmailChannel({
        "smtp_host": "smtp.example.com",
        "from_addr": "bot@corp.com",
        "to_addr": "dev@corp.com",
    })
    assert channel.healthcheck() is True


def test_email_channel_handles_bad_port() -> None:
    """A non-numeric SMTP_PORT falls back to 587 instead of crashing."""
    channel = EmailChannel({
        "smtp_host": "smtp.example.com",
        "smtp_port": "not-a-number",
        "from_addr": "bot@corp.com",
        "to_addr": "dev@corp.com",
    })
    assert channel._port == 587
