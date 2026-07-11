"""Telegram notification channel.

Talks to the Telegram Bot API (``https://api.telegram.org``) over HTTPS using
:mod:`httpx`. The client is created with ``trust_env=True`` so corporate proxy
and CA-bundle environment variables (``HTTP_PROXY``/``HTTPS_PROXY``/
``SSL_CERT_FILE``) are honoured automatically, matching the project's network
configuration policy.

``httpx`` is an optional dependency: install it via the ``telegram`` extra
(``pip install -e '.[telegram]'``). If it is not installed, importing this
module still succeeds but :class:`TelegramChannel` raises a clear
:class:`RuntimeError` at construction time so the system keeps working without
Telegram. The factory skips the ``telegram`` channel when httpx is missing.

Besides one-way notifications (:meth:`TelegramChannel.send`), this module also
exposes the interactive helpers (:meth:`send_with_inline_keyboard`,
:meth:`wait_for_callback_query`, :meth:`wait_for_text_reply`) used by
:class:`devflow.telegram_bridge.TelegramBridge` to implement human-in-the-loop
plan approval.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from devflow.notifications.base import NotificationChannel

logger = logging.getLogger(__name__)

try:
    import httpx
except ImportError:  # pragma: no cover - optional dependency
    httpx = None  # type: ignore[assignment]

DEFAULT_API_BASE = "https://api.telegram.org"
DEFAULT_TIMEOUT = 30.0
DEFAULT_POLL_LONG_POLL_TIMEOUT = 25  # seconds requested from getUpdates
TEXT_REPLY_TIMEOUT = 300  # default seconds to wait for a free-form text answer


class TelegramChannel(NotificationChannel):
    """Publish messages and run interactive flows via the Telegram Bot API."""

    name = "telegram"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        if httpx is None:
            raise RuntimeError(
                "Telegram channel requires the 'httpx' package. "
                "Install it with: pip install -e '.[telegram]'"
            )
        self.bot_token = (config.get("bot_token") or "").strip()
        self.chat_id = str(config.get("chat_id") or "").strip()
        if not self.bot_token:
            raise ValueError("Telegram channel requires 'bot_token' in config")
        if not self.chat_id:
            raise ValueError("Telegram channel requires 'chat_id' in config")

        self.api_base = (config.get("api_base") or DEFAULT_API_BASE).rstrip("/")
        timeout = float(config.get("timeout", DEFAULT_TIMEOUT))
        # trust_env=True applies HTTP_PROXY/HTTPS_PROXY/SSL_CERT_FILE from env,
        # consistent with how the project forwards proxy/CA settings to MCP.
        self._client = httpx.Client(
            base_url=self.api_base,
            timeout=timeout,
            trust_env=True,
        )
        # Offset for getUpdates long-polling; persists across calls within a run
        # so already-consumed updates are not re-read.
        self._update_offset: int | None = None

    # -- internals ---------------------------------------------------------

    def _api_url(self, method: str) -> str:
        return f"/bot{self.bot_token}/{method}"

    def _post(self, method: str, payload: dict[str, Any]) -> Any:
        """POST to a Bot API method and return the parsed ``result`` object.

        Raises :class:`RuntimeError` on transport errors, non-2xx responses, or
        when Telegram reports ``ok: false``.
        """
        try:
            resp = self._client.post(self._api_url(method), json=payload)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Telegram API transport error on {method}: {exc}") from exc

        if resp.status_code >= 400:
            raise RuntimeError(
                f"Telegram API {method} returned HTTP {resp.status_code}: {resp.text}"
            )
        try:
            body = resp.json()
        except ValueError as exc:
            raise RuntimeError(f"Telegram API {method} returned non-JSON: {resp.text}") from exc

        if not body.get("ok"):
            description = body.get("description", "unknown error")
            raise RuntimeError(f"Telegram API {method} failed: {description}")
        return body.get("result") or {}

    def _get(self, method: str, params: dict[str, Any]) -> Any:
        """GET wrapper for Bot API methods (used by healthcheck)."""
        try:
            resp = self._client.get(self._api_url(method), params=params)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Telegram API transport error on {method}: {exc}") from exc

        if resp.status_code >= 400:
            raise RuntimeError(
                f"Telegram API {method} returned HTTP {resp.status_code}: {resp.text}"
            )
        try:
            body = resp.json()
        except ValueError as exc:
            raise RuntimeError(f"Telegram API {method} returned non-JSON: {resp.text}") from exc

        if not body.get("ok"):
            description = body.get("description", "unknown error")
            raise RuntimeError(f"Telegram API {method} failed: {description}")
        return body.get("result") or {}

    # -- NotificationChannel interface -------------------------------------

    def send(self, message: str, *, parse_mode: str | None = "Markdown") -> str:
        """Send a text message and return ``telegram://message/<id>``."""
        result = self._post(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": message,
                **({"parse_mode": parse_mode} if parse_mode else {}),
            },
        )
        message_id = result.get("message_id", 0)
        return f"telegram://message/{message_id}"

    def healthcheck(self) -> bool:
        """Return True when ``getMe`` succeeds."""
        try:
            self._get("getMe", {})
            return True
        except Exception as exc:  # pragma: no cover - network dependency
            logger.debug("Telegram healthcheck failed: %s", exc)
            return False

    def close(self) -> None:
        self._client.close()

    # -- interactive helpers (for TelegramBridge) --------------------------

    def send_with_inline_keyboard(
        self,
        message: str,
        buttons: list[list[dict[str, str]]],
        *,
        parse_mode: str | None = "Markdown",
    ) -> int:
        """Send a message with an inline keyboard and return its ``message_id``.

        ``buttons`` is a list of rows; each row is a list of button dicts with
        ``text`` and ``callback_data`` keys.
        """
        result = self._post(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": message,
                "reply_markup": {"inline_keyboard": buttons},
                **({"parse_mode": parse_mode} if parse_mode else {}),
            },
        )
        message_id = result.get("message_id", 0)
        if not message_id:
            raise RuntimeError("Telegram sendMessage did not return a message_id")
        return int(message_id)

    def wait_for_callback_query(
        self,
        message_id: int,
        *,
        timeout: float = TEXT_REPLY_TIMEOUT,
    ) -> str:
        """Long-poll ``getUpdates`` until a callback query on ``message_id`` arrives.

        Returns the ``callback_data`` string. Calls ``answerCallbackQuery`` to
        dismiss the loading indicator on the client. Raises :class:`TimeoutError`
        when no matching callback arrives within ``timeout`` seconds.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            params: dict[str, Any] = {
                "timeout": DEFAULT_POLL_LONG_POLL_TIMEOUT,
                "allowed_updates": "callback_query,message",
            }
            if self._update_offset is not None:
                params["offset"] = self._update_offset

            try:
                updates = self._get("getUpdates", params)
            except RuntimeError as exc:
                logger.warning("getUpdates error, retrying: %s", exc)
                time.sleep(1)
                continue

            for update in updates or []:
                self._update_offset = int(update.get("update_id", 0)) + 1
                cb = update.get("callback_query")
                if not cb:
                    continue
                cb_message = cb.get("message") or {}
                if int(cb_message.get("message_id", 0)) != message_id:
                    continue
                callback_data = cb.get("data", "")
                query_id = cb.get("id", "")
                # Dismiss the loading spinner on the button.
                try:
                    self._post("answerCallbackQuery", {"callback_query_id": query_id})
                except RuntimeError as exc:
                    logger.debug("answerCallbackQuery failed: %s", exc)
                return str(callback_data)

        raise TimeoutError(
            f"No Telegram callback query for message {message_id} within {timeout}s"
        )

    def wait_for_text_reply(self, *, timeout: float = TEXT_REPLY_TIMEOUT) -> str:
        """Long-poll ``getUpdates`` until a free-form text message arrives.

        Used to collect a rejection reason or a list of requested changes after
        the user taps the corresponding inline button.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            params: dict[str, Any] = {
                "timeout": DEFAULT_POLL_LONG_POLL_TIMEOUT,
                "allowed_updates": "message",
            }
            if self._update_offset is not None:
                params["offset"] = self._update_offset

            try:
                updates = self._get("getUpdates", params)
            except RuntimeError as exc:
                logger.warning("getUpdates error, retrying: %s", exc)
                time.sleep(1)
                continue

            for update in updates or []:
                self._update_offset = int(update.get("update_id", 0)) + 1
                msg = update.get("message")
                if not msg:
                    continue
                text = (msg.get("text") or "").strip()
                if text:
                    return text

        raise TimeoutError(f"No Telegram text reply within {timeout}s")
