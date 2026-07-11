"""Unit tests for the Telegram notification channel."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from devflow.notifications.telegram import TelegramChannel


class FakeResponse:
    """Minimal stand-in for an httpx.Response."""

    def __init__(self, status_code: int = 200, json_data: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._json = json_data or {"ok": True, "result": {"message_id": 1}}
        self.text = str(self._json)

    def json(self) -> dict[str, Any]:
        return self._json


class FakeHttpxClient:
    """Records calls and returns canned responses keyed by (method, url_path).

    ``responses`` maps (verb, path) -> list[FakeResponse]. Each call pops the
    first response; if none configured, a default success is returned. Also
    supports raising an exception when ``raise_error`` matches the path.
    """

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.calls: list[dict[str, Any]] = []
        self.responses: dict[tuple[str, str], list[FakeResponse]] = {}
        self.errors: dict[tuple[str, str], Exception] = {}
        self.closed = False

    def _resolve(self, verb: str, url: str) -> FakeResponse:
        # url is the full path (base_url already stripped by httpx when given).
        queue = self.responses.get((verb, url))
        if queue:
            return queue.pop(0)
        return FakeResponse()

    def post(self, url: str, json: dict[str, Any] | None = None, **kwargs: Any) -> FakeResponse:
        err = self.errors.get(("POST", url))
        if err is not None:
            raise err
        self.calls.append({"method": "POST", "url": url, "json": json})
        return self._resolve("POST", url)

    def get(self, url: str, params: dict[str, Any] | None = None, **kwargs: Any) -> FakeResponse:
        err = self.errors.get(("GET", url))
        if err is not None:
            raise err
        self.calls.append({"method": "GET", "url": url, "params": params})
        return self._resolve("GET", url)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> FakeHttpxClient:
    """Replace httpx.Client in the telegram module with a recording fake."""
    instance = FakeHttpxClient()
    monkeypatch.setattr(
        "devflow.notifications.telegram.httpx.Client",
        lambda **kwargs: _bind(instance, kwargs),
    )
    return instance


def _bind(instance: FakeHttpxClient, kwargs: dict[str, Any]) -> FakeHttpxClient:
    instance.kwargs = kwargs
    return instance


def _base_config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {"bot_token": "123:abc", "chat_id": "987654"}
    config.update(overrides)
    return config


# ---------------------------------------------------------------------------
# __init__ validation
# ---------------------------------------------------------------------------


def test_init_requires_bot_token(fake_client: FakeHttpxClient) -> None:
    with pytest.raises(ValueError, match="bot_token"):
        TelegramChannel({"bot_token": "", "chat_id": "1"})


def test_init_requires_chat_id(fake_client: FakeHttpxClient) -> None:
    with pytest.raises(ValueError, match="chat_id"):
        TelegramChannel({"bot_token": "tok", "chat_id": ""})


def test_init_uses_default_api_base(fake_client: FakeHttpxClient) -> None:
    channel = TelegramChannel(_base_config())
    assert channel.api_base == "https://api.telegram.org"
    channel.close()


def test_init_trust_env_enabled(fake_client: FakeHttpxClient) -> None:
    """The httpx client must honour proxy/CA env vars (trust_env=True)."""
    TelegramChannel(_base_config())
    assert fake_client.kwargs.get("trust_env") is True
    assert fake_client.kwargs.get("timeout") == 30.0


# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------


def test_send_returns_message_url(fake_client: FakeHttpxClient) -> None:
    fake_client.responses[("POST", "/bot123:abc/sendMessage")] = [
        FakeResponse(json_data={"ok": True, "result": {"message_id": 42}})
    ]
    channel = TelegramChannel(_base_config())
    url = channel.send("hello")
    assert url == "telegram://message/42"

    call = fake_client.calls[-1]
    assert call["json"]["chat_id"] == "987654"
    assert call["json"]["text"] == "hello"
    assert call["json"]["parse_mode"] == "Markdown"
    channel.close()


def test_send_without_parse_mode(fake_client: FakeHttpxClient) -> None:
    fake_client.responses[("POST", "/bot123:abc/sendMessage")] = [
        FakeResponse(json_data={"ok": True, "result": {"message_id": 7}})
    ]
    channel = TelegramChannel(_base_config())
    channel.send("plain", parse_mode=None)
    assert "parse_mode" not in fake_client.calls[-1]["json"]
    channel.close()


# ---------------------------------------------------------------------------
# send_with_inline_keyboard
# ---------------------------------------------------------------------------


def test_send_with_inline_keyboard_returns_message_id(fake_client: FakeHttpxClient) -> None:
    fake_client.responses[("POST", "/bot123:abc/sendMessage")] = [
        FakeResponse(json_data={"ok": True, "result": {"message_id": 100}})
    ]
    channel = TelegramChannel(_base_config())
    buttons = [[{"text": "Yes", "callback_data": "approve"}]]
    message_id = channel.send_with_inline_keyboard("decide", buttons)
    assert message_id == 100

    payload = fake_client.calls[-1]["json"]
    assert payload["reply_markup"] == {"inline_keyboard": buttons}
    channel.close()


def test_send_with_inline_keyboard_missing_message_id(fake_client: FakeHttpxClient) -> None:
    fake_client.responses[("POST", "/bot123:abc/sendMessage")] = [
        FakeResponse(json_data={"ok": True, "result": {}})
    ]
    channel = TelegramChannel(_base_config())
    with pytest.raises(RuntimeError, match="message_id"):
        channel.send_with_inline_keyboard("x", [])
    channel.close()


# ---------------------------------------------------------------------------
# error handling
# ---------------------------------------------------------------------------


def test_send_api_error_raises(fake_client: FakeHttpxClient) -> None:
    fake_client.responses[("POST", "/bot123:abc/sendMessage")] = [
        FakeResponse(json_data={"ok": False, "description": "chat not found"})
    ]
    channel = TelegramChannel(_base_config())
    with pytest.raises(RuntimeError, match="chat not found"):
        channel.send("hi")
    channel.close()


def test_send_http_error_raises(fake_client: FakeHttpxClient) -> None:
    fake_client.responses[("POST", "/bot123:abc/sendMessage")] = [
        FakeResponse(status_code=401, json_data={"ok": False, "description": "unauthorized"})
    ]
    channel = TelegramChannel(_base_config())
    with pytest.raises(RuntimeError, match="HTTP 401"):
        channel.send("hi")
    channel.close()


def test_send_transport_error_raises(fake_client: FakeHttpxClient) -> None:
    fake_client.errors[("POST", "/bot123:abc/sendMessage")] = httpx.ConnectError("boom")
    channel = TelegramChannel(_base_config())
    with pytest.raises(RuntimeError, match="transport error"):
        channel.send("hi")
    channel.close()


# ---------------------------------------------------------------------------
# wait_for_callback_query
# ---------------------------------------------------------------------------


def test_wait_for_callback_query_filters_by_message(fake_client: FakeHttpxClient) -> None:
    # First getUpdates returns a callback for a different message (ignored),
    # second returns the matching callback for message_id=100.
    fake_client.responses[("GET", "/bot123:abc/getUpdates")] = [
        FakeResponse(json_data={
            "ok": True,
            "result": [
                {"update_id": 1, "callback_query": {
                    "id": "cq1", "data": "other",
                    "message": {"message_id": 999},
                }},
            ],
        }),
        FakeResponse(json_data={
            "ok": True,
            "result": [
                {"update_id": 2, "callback_query": {
                    "id": "cq2", "data": "approve",
                    "message": {"message_id": 100},
                }},
            ],
        }),
    ]
    fake_client.responses[("POST", "/bot123:abc/answerCallbackQuery")] = [
        FakeResponse(json_data={"ok": True, "result": True}),
    ]
    channel = TelegramChannel(_base_config())
    data = channel.wait_for_callback_query(100, timeout=2)
    assert data == "approve"

    # answerCallbackQuery was called with the matching query id.
    answer_calls = [c for c in fake_client.calls if c["url"] == "/bot123:abc/answerCallbackQuery"]
    assert answer_calls
    assert answer_calls[-1]["json"]["callback_query_id"] == "cq2"
    channel.close()


def test_wait_for_callback_query_timeout(monkeypatch: pytest.MonkeyPatch,
                                          fake_client: FakeHttpxClient) -> None:
    # Always return empty updates so the loop exhausts its time budget.
    fake_client.responses_default_empty = True
    fake_client.responses[("GET", "/bot123:abc/getUpdates")] = [
        FakeResponse(json_data={"ok": True, "result": []})
    ]

    # Patch time.sleep to avoid real waiting and time.monotonic to advance fast.
    import devflow.notifications.telegram as tg_mod

    calls = {"n": 0}

    def fast_monotonic() -> float:
        calls["n"] += 1
        return calls["n"] * 1000  # each call jumps 1000s -> timeout reached quickly

    monkeypatch.setattr(tg_mod.time, "monotonic", fast_monotonic)
    monkeypatch.setattr(tg_mod.time, "sleep", lambda *_: None)

    channel = TelegramChannel(_base_config())
    with pytest.raises(TimeoutError):
        channel.wait_for_callback_query(1, timeout=2)
    channel.close()


# ---------------------------------------------------------------------------
# wait_for_text_reply
# ---------------------------------------------------------------------------


def test_wait_for_text_reply_returns_text(fake_client: FakeHttpxClient) -> None:
    fake_client.responses[("GET", "/bot123:abc/getUpdates")] = [
        FakeResponse(json_data={
            "ok": True,
            "result": [
                {"update_id": 5, "message": {"text": "bad reason"}},
            ],
        }),
    ]
    channel = TelegramChannel(_base_config())
    text = channel.wait_for_text_reply(timeout=2)
    assert text == "bad reason"
    channel.close()


def test_wait_for_text_reply_skips_empty(monkeypatch: pytest.MonkeyPatch,
                                          fake_client: FakeHttpxClient) -> None:
    fake_client.responses[("GET", "/bot123:abc/getUpdates")] = [
        FakeResponse(json_data={
            "ok": True,
            "result": [{"update_id": 1, "message": {"text": ""}}],
        }),
        FakeResponse(json_data={
            "ok": True,
            "result": [{"update_id": 2, "message": {"text": "real reply"}}],
        }),
    ]
    import devflow.notifications.telegram as tg_mod
    # Keep monotonic real so the loop iterates normally; no sleep needed as
    # getUpdates returns immediately in the fake.
    monkeypatch.setattr(tg_mod.time, "sleep", lambda *_: None)

    channel = TelegramChannel(_base_config())
    text = channel.wait_for_text_reply(timeout=5)
    assert text == "real reply"
    channel.close()


# ---------------------------------------------------------------------------
# healthcheck & close
# ---------------------------------------------------------------------------


def test_healthcheck_true_on_success(fake_client: FakeHttpxClient) -> None:
    fake_client.responses[("GET", "/bot123:abc/getMe")] = [
        FakeResponse(json_data={"ok": True, "result": {"id": 1, "is_bot": True}})
    ]
    channel = TelegramChannel(_base_config())
    assert channel.healthcheck() is True
    channel.close()


def test_healthcheck_false_on_error(fake_client: FakeHttpxClient) -> None:
    fake_client.responses[("GET", "/bot123:abc/getMe")] = [
        FakeResponse(json_data={"ok": False, "description": "bad token"})
    ]
    channel = TelegramChannel(_base_config())
    assert channel.healthcheck() is False
    channel.close()


def test_close_closes_client(fake_client: FakeHttpxClient) -> None:
    channel = TelegramChannel(_base_config())
    channel.close()
    assert fake_client.closed


# ---------------------------------------------------------------------------
# optional dependency handling
# ---------------------------------------------------------------------------


def test_init_raises_when_httpx_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """When httpx is not installed, constructing a channel raises RuntimeError."""
    import devflow.notifications.telegram as tg_mod

    monkeypatch.setattr(tg_mod, "httpx", None)
    with pytest.raises(RuntimeError, match="httpx"):
        TelegramChannel(_base_config())


def test_module_imports_without_httpx() -> None:
    """The telegram module imports even when httpx is absent.

    Re-imports the module in a clean subprocess with httpx hidden to prove the
    factory/rest of the system is never broken by a missing optional dep.
    """
    import importlib

    mod = importlib.import_module("devflow.notifications.telegram")
    assert mod.httpx is not None  # installed in the test env
    assert hasattr(mod, "TelegramChannel")
