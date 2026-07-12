"""Unit tests for the ntfy notification channel."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from devflow.notifications.ntfy import NtfyChannel


def test_ntfy_channel_name() -> None:
    """The channel name is 'ntfy'."""
    channel = NtfyChannel({"topic": "test-topic"})
    assert channel.name == "ntfy"


def test_ntfy_send_publishes_message() -> None:
    """send() POSTs the message to the ntfy server."""
    channel = NtfyChannel({"topic": "my-topic", "server": "https://ntfy.sh"})

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()

    with patch("devflow.notifications.ntfy.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value.__enter__.return_value = mock_client

        result = channel.send("Hello ntfy", parse_mode=None)

    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert call_args[0][0] == "https://ntfy.sh/my-topic"
    assert "Hello ntfy" in call_args[1]["content"]
    assert result is not None
    assert "my-topic" in result


def test_ntfy_send_with_custom_server() -> None:
    """send() uses a custom server URL when configured."""
    channel = NtfyChannel({"topic": "dev", "server": "https://ntfy.internal.corp"})

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()

    with patch("devflow.notifications.ntfy.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value.__enter__.return_value = mock_client

        channel.send("test")

    call_args = mock_client.post.call_args
    assert call_args[0][0] == "https://ntfy.internal.corp/dev"


def test_ntfy_healthcheck_without_topic_returns_false() -> None:
    """healthcheck() returns False when no topic is configured."""
    channel = NtfyChannel({})
    assert channel.healthcheck() is False


def test_ntfy_healthcheck_with_topic_returns_true() -> None:
    """healthcheck() returns True when a topic is configured."""
    channel = NtfyChannel({"topic": "my-topic"})
    assert channel.healthcheck() is True
