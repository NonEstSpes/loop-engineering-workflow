"""Tests for the /api/events SSE endpoint.

The SSE endpoint streams indefinitely, so consuming it through
``fastapi.testclient.TestClient.stream`` (which runs the app in a portal/thread
and waits for response bytes) is flaky/hangs: the streaming response never
finishes its body and no chunk is emitted until an event is published or the
15s ping fires. Instead we drive the ASGI app directly: we can read the
``http.response.start`` message for status/headers and feed ``http.disconnect``
once we have seen the event we care about. This is deterministic and fast.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from devflow.config import Config
from devflow.daemon.events import GLOBAL_TOPIC, EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.daemon.web import create_app


def _make_app(mock_config: Config) -> tuple[Any, EventBus]:
    """Build a daemon app wired with a fresh EventBus."""
    bus = EventBus()
    app = create_app(mock_config, DaemonLocks(), bus)
    return app, bus


async def _drive_asgi(
    app: Any,
    bus: EventBus,
    *,
    publish: dict[str, Any] | None = None,
) -> tuple[int, str, bytes]:
    """Invoke ``GET /api/events`` at the ASGI level.

    Returns ``(status, content_type, body_bytes)``. If ``publish`` is given,
    the event is published on the GLOBAL topic after the request body has been
    received and before signalling ``http.disconnect``, so the generator yields
    exactly one real event frame (plus possibly the ping). Body accumulation
    stops as soon as the published event is observed or the app disconnects.
    """
    start: dict[str, Any] = {}
    body_chunks: list[bytes] = []
    state = {"received_request": False, "published": False}

    async def receive() -> dict[str, Any]:
        # First receive: hand over the (empty) request body.
        if not state["received_request"]:
            state["received_request"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        # Subsequent receives: the app is awaiting its next ASGI input while the
        # generator is blocked on queue.get(). Publish then disconnect.
        if publish is not None and not state["published"]:
            state["published"] = True
            await asyncio.sleep(0.05)  # let the subscribe() coroutine run
            await bus.publish(GLOBAL_TOPIC, publish)
            await asyncio.sleep(0.2)  # let the frame flush
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            start["status"] = message["status"]
            headers = {
                (k.decode() if isinstance(k, bytes) else k): (
                    v.decode() if isinstance(v, bytes) else v
                )
                for k, v in message["headers"]
            }
            start["content_type"] = headers.get("content-type", "")
        elif message["type"] == "http.response.body":
            body_chunks.append(message.get("body", b""))
            if publish is not None and _contains(
                b"".join(body_chunks), publish.get("event", "")
            ):
                raise asyncio.CancelledError("observed-published-event")

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/events",
        "raw_path": b"/api/events",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
        "app": app,
        "state": {},
    }
    with contextlib.suppress(TimeoutError, asyncio.CancelledError):
        await asyncio.wait_for(app(scope, receive, send), timeout=5.0)
    return start.get("status", 0), start.get("content_type", ""), b"".join(body_chunks)


def _contains(haystack: bytes, needle: str) -> bool:
    return needle.encode() in haystack if needle else False


def test_sse_endpoint_registered_with_correct_content_type(
    mock_config: Config,
) -> None:
    """GET /api/events exists, returns 200 with text/event-stream content-type."""
    app, bus = _make_app(mock_config)
    status, content_type, _ = asyncio.run(_drive_asgi(app, bus))
    assert status == 200
    assert "text/event-stream" in content_type


def test_sse_endpoint_streams_published_events(mock_config: Config) -> None:
    """A published event is forwarded as an SSE frame with event: and data:."""
    app, bus = _make_app(mock_config)
    status, content_type, body = asyncio.run(
        _drive_asgi(
            app,
            bus,
            publish={"event": "task.started", "task_id": "T-1"},
        )
    )
    assert status == 200
    assert "text/event-stream" in content_type
    text = body.decode("utf-8", errors="replace")
    assert "event: task.started" in text
    assert "data:" in text
    assert '"task_id": "T-1"' in text
