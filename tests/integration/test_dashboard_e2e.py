"""Integration test: dashboard backend (SSE + tasks + static) end-to-end.

Verifies the Phase 5 backend stack works together:
- /api/events SSE delivers a published event
- /api/tasks/* routes reflect runner state + batch store
- static SPA fallback serves index.html for unknown non-/api paths
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi.testclient import TestClient

from devflow.batch.eod_handler import EodHandler
from devflow.batch.models import BatchEntry, BatchStatus
from devflow.batch.store import BatchStore
from devflow.config import Config
from devflow.daemon.events import GLOBAL_TOPIC, EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.daemon.web import create_app
from devflow.schemas import ReporterResponse
from devflow.state import FinalVerdict


def _make_entry(task_id: str) -> BatchEntry:
    return BatchEntry(
        task_id=task_id,
        task_title=f"Task {task_id}",
        branch_name=f"devflow/{task_id}/abc",
        worktree_path="/tmp/wt",
        diff="d",
        plan_summary="s",
        plan_steps=["step 1"],
        checker_reports=[],
        self_review_notes="",
        final_verdict=FinalVerdict.APPROVE,
        reporter_artifacts=ReporterResponse(
            pr_title="t", pr_description="d", corporate_report="r", commit_message="c"
        ),
        status=BatchStatus.PUBLISHED,
        created_at="2026-07-13T10:00:00Z",
    )


def test_dashboard_reflects_published_task(
    mock_config: Config, tmp_path: Any
) -> None:
    """A published task appears in /api/tasks/done and /api/tasks/{id}."""
    store = BatchStore(str(tmp_path / "batch_store.db"))
    store.add(_make_entry("T-1"))
    handler = EodHandler(mock_config, store, EventBus(), repo_path=".")
    bus = EventBus()
    app = create_app(mock_config, DaemonLocks(), bus, eod_handler=handler)
    client = TestClient(app)

    # /api/tasks/done lists it.
    done = client.get("/api/tasks/done").json()
    assert any(e["task_id"] == "T-1" for e in done)

    # /api/tasks/T-1 returns full detail.
    detail = client.get("/api/tasks/T-1").json()
    assert detail["task_id"] == "T-1"
    assert detail["reporter_artifacts"]["pr_title"] == "t"

    store.close()


# --- SSE: drive the ASGI app directly (TestClient.stream hangs for SSE) ---------
# See tests/unit/daemon/test_web_sse.py for why: sse-starlette v3 emits no chunk
# until an event arrives, so TestClient.stream (which runs the app in a portal and
# waits for response bytes) never terminates. Driving ASGI directly lets us feed
# http.disconnect after we observe the event frame, deterministically and fast.


async def _drive_sse(
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
            needle = publish.get("event", "") if publish else ""
            if needle and needle.encode() in b"".join(body_chunks):
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


def test_sse_delivers_published_event(mock_config: Config) -> None:
    """An event published to the EventBus is delivered via /api/events SSE.

    Uses the ASGI-driver pattern from tests/unit/daemon/test_web_sse.py because
    ``TestClient.stream("GET", "/api/events")`` hangs: sse-starlette emits no
    chunk until an event arrives, so the streaming response never completes its
    body and TestClient's portal wait never returns.
    """
    bus = EventBus()
    app = create_app(mock_config, DaemonLocks(), bus)

    status, content_type, body = asyncio.run(
        _drive_sse(
            app,
            bus,
            publish={"event": "eod.ready", "pending_count": 2},
        )
    )
    assert status == 200
    assert "text/event-stream" in content_type
    text = body.decode("utf-8", errors="replace")
    assert "event: eod.ready" in text
    assert "data:" in text
    assert '"pending_count": 2' in text


def test_spa_fallback_serves_index(mock_config: Config, tmp_path: Any) -> None:
    """In production mode, unknown non-/api paths serve index.html."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>SPA</html>", encoding="utf-8")
    mock_config.workflow.daemon.frontend_dist = str(dist)

    bus = EventBus()
    app = create_app(mock_config, DaemonLocks(), bus)
    client = TestClient(app)

    # API still works.
    assert client.get("/api/health").status_code == 200
    # SPA route serves index.
    assert "SPA" in client.get("/some/route").text
    # /api/unknown is still 404 (not intercepted by SPA fallback).
    assert client.get("/api/nonexistent").status_code == 404
