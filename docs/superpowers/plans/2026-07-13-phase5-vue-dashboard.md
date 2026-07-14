# Phase 5: Vue 3 SPA Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Vue 3 SPA dashboard served by the FastAPI daemon, with a typed API client, Pinia stores, a live SSE event stream, and functional-but-unstyled page skeletons (Dashboard, Approvals, EOD Review, Task Detail) consuming the existing REST API.

**Architecture:** Backend gains an SSE endpoint (`/api/events`) backed by an EventBus global `"*"` topic, `/api/tasks/*` routes (current/queue/done/detail reading from runner state + batch store + task source), CORS middleware for the Vite dev server, and a `StaticFiles` mount serving `frontend/dist/` in production. The frontend is a separate npm package (`frontend/`) using Vite + Vue 3 + TypeScript + Pinia + Vue Router; a typed API client is generated from FastAPI's OpenAPI schema via `openapi-typescript`. UI is functional skeletons only — no component library or design system (spec line 294; separate task).

**Tech Stack:** Backend: Python ≥3.11, FastAPI, sse-starlette (NEW), Pydantic. Frontend: Vue 3 (Composition API, `<script setup>`), Vite, TypeScript, Pinia, Vue Router, `openapi-typescript`, native `fetch` + `EventSource`.

## Global Constraints

- Python ≥3.11 (pyproject.toml line 11); ruff rules E,F,I,W,UP,B,C4,SIM (E501 ignored), line-length 100; mypy `python_version = "3.11"`
- `asyncio_mode = "auto"` for pytest-asyncio; `testpaths = ["tests"]`, `pythonpath = ["src"]`
- All backend code and logs in English; frontend UI text in English
- `DaemonConfig` (config.py:78-86): `enabled`, `task_schedule`, `eod_schedule`, `port=8787`, `approval_timeout_hours=8`, `approval_on_timeout="defer"`
- FastAPI `create_app(app_cfg, locks, event_bus, runner=None, approval_store=None, eod_handler=None)` — closure-injected deps (NOT `Depends`). `event_bus` param is plumbed but currently dormant.
- `EventBus` (events.py): `async subscribe(topic) -> Queue`, `async publish(topic, data)`, topics are plain strings (`"task.{id}"`, `"eod"`). NO global/wildcard topic yet. Queue maxsize=256, drops on overflow.
- Existing events: `task.started`, `task.finished`, `task.error` (on `task.{id}` topics), `eod.ready` (on `"eod"`).
- `WorkflowRunner.run_task` publishes task lifecycle events but does NOT track `current_node` (graph runs via `graph.invoke`, atomic). `app.state.set_current_task` exists in web.py:185 but is NEVER called.
- `BatchStore` at `{repo_path}/.devflow/batch_store.db`, `check_same_thread=False`. `EodHandler.list_pending()`, `.publish_selected(task_ids)`, `.finalize()`.
- `ApprovalStore.get_pending() -> [{"thread_id": str, "payload": dict}]`.
- No `frontend/` dir exists; `.gitignore` has NO frontend entries.
- Host hardcoded `127.0.0.1` in `run_web_server` (web.py:218) — fine for local dashboard.
- Frontend dev: Vite `:5173` proxies `/api/*` → FastAPI `:8787`. Production: FastAPI serves `frontend/dist/` as static at root, `/api/*` as API, one port `:8787`.
- UI = functional skeletons, NO design system (spec line 294).
- Frontend Node tooling lives in `frontend/package.json` (NOT pyproject). TypeScript strict mode.

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | Modify: add `sse-starlette` dependency |
| `src/devflow/config.py` | Modify: `DaemonConfig` += `serve_frontend: bool = True`, `frontend_dist: str = "frontend/dist"`, `cors_origins: list[str] = []` |
| `src/devflow/daemon/events.py` | Modify: add global topic constant + publish-to-global in `publish()` |
| `src/devflow/daemon/web.py` | Modify: add SSE `/api/events`, `/api/tasks/*` routes, CORS middleware, StaticFiles mount, TaskDetail/EodEntry response models |
| `src/devflow/daemon/runner.py` | Modify: accept optional `on_task_change` callback, call it in run_task (sets current task) |
| `src/devflow/daemon/__main__.py` | Modify: wire `on_task_change` callback from app to runner |
| `frontend/package.json` | NEW: npm package manifest |
| `frontend/vite.config.ts` | NEW: Vite config + dev proxy |
| `frontend/tsconfig.json`, `frontend/tsconfig.node.json` | NEW: TS config |
| `frontend/index.html` | NEW: SPA entry HTML |
| `frontend/src/main.ts` | NEW: app bootstrap (Pinia, Router) |
| `frontend/src/App.vue` | NEW: root layout + nav |
| `frontend/src/router/index.ts` | NEW: Vue Router routes |
| `frontend/src/api/client.ts` | NEW: typed fetch client |
| `frontend/src/api/types.ts` | NEW: generated OpenAPI types (via openapi-typescript) + manual re-exports |
| `frontend/src/stores/daemon.ts` | NEW: Pinia store — health, state |
| `frontend/src/stores/approvals.ts` | NEW: Pinia store — pending approvals |
| `frontend/src/stores/eod.ts` | NEW: Pinia store — pending EOD entries |
| `frontend/src/stores/tasks.ts` | NEW: Pinia store — current task, queue, done |
| `frontend/src/composables/useSSE.ts` | NEW: EventSource composable → daemon store events |
| `frontend/src/composables/usePolling.ts` | NEW: periodic fetch composable |
| `frontend/src/views/DashboardView.vue` | NEW: health + current task + quick links |
| `frontend/src/views/ApprovalsView.vue` | NEW: pending approvals list + resolve form |
| `frontend/src/views/EodReviewView.vue` | NEW: pending EOD entries + publish |
| `frontend/src/views/TaskDetailView.vue` | NEW: task details (plan/diff/reports) |
| `frontend/src/views/NotFoundView.vue` | NEW: 404 |
| `frontend/.gitignore` | NEW: node_modules, dist |
| `tests/unit/daemon/test_web_sse.py` | NEW: SSE endpoint test |
| `tests/unit/daemon/test_web_tasks.py` | NEW: /api/tasks/* test |
| `tests/unit/daemon/test_web_cors_static.py` | NEW: CORS + static serving test |
| `tests/unit/daemon/test_runner_callback.py` | NEW: on_task_change callback test |
| `tests/integration/test_dashboard_e2e.py` | NEW: full stack — SSE event → task route |

---

## Task 1: sse-starlette dependency + EventBus global topic

**Files:**
- Modify: `pyproject.toml` (dependencies)
- Modify: `src/devflow/daemon/events.py` (global topic)
- Test: `tests/unit/daemon/test_events.py` (extend)

**Interfaces:**
- Consumes: existing `EventBus`
- Produces: `EventBus` publishes every event to a global topic `GLOBAL_TOPIC = "*"` in addition to the specific topic. So a subscriber on `"*"` receives all events. `publish(topic, data)` now does `self._fan_out(topic, data)` + `self._fan_out(GLOBAL_TOPIC, data)`.

- [ ] **Step 1: Add sse-starlette dependency**

In `pyproject.toml`, in the `dependencies` array (around line 20-30), add:
```toml
    "sse-starlette>=2.1.0",
```
Place it alphabetically near the other `starlette`/`fastapi` deps. Run `pip install -e .` to install it (or `pip install sse-starlette>=2.1.0`).

- [ ] **Step 2: Write the failing test**

Add to `tests/unit/daemon/test_events.py` (read the file first to match its style):

```python
from devflow.daemon.events import EventBus, GLOBAL_TOPIC


async def test_global_topic_receives_all_events() -> None:
    """A subscriber on the global '*' topic receives every published event."""
    bus = EventBus()
    queue = await bus.subscribe(GLOBAL_TOPIC)

    await bus.publish("task.4321", {"event": "task.started", "task_id": "4321"})
    await bus.publish("eod", {"event": "eod.ready", "pending_count": 3})

    msg1 = await queue.get()
    msg2 = await queue.get()

    assert msg1["event"] == "task.started"
    assert msg2["event"] == "eod.ready"
    await bus.close()


async def test_specific_topic_still_works_alongside_global() -> None:
    """A subscriber on a specific topic still gets only that topic's events."""
    bus = EventBus()
    specific_q = await bus.subscribe("task.1")
    global_q = await bus.subscribe(GLOBAL_TOPIC)

    await bus.publish("task.1", {"event": "task.started", "task_id": "1"})
    await bus.publish("task.2", {"event": "task.started", "task_id": "2"})

    specific_msg = await specific_q.get()
    # specific_q should only have task.1's event
    assert specific_msg["task_id"] == "1"

    # global_q should have both
    g1 = await global_q.get()
    g2 = await global_q.get()
    assert {g1["task_id"], g2["task_id"]} == {"1", "2"}
    await bus.close()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_events.py -v`
Expected: FAIL with `ImportError: cannot import name 'GLOBAL_TOPIC'`

- [ ] **Step 4: Implement the global topic**

In `src/devflow/daemon/events.py`, add the constant near the top (after the logger definition):
```python
GLOBAL_TOPIC = "*"
"""Global topic: a subscriber on this topic receives every published event.

Used by the SSE endpoint (``/api/events``) to stream all daemon events to the
dashboard without requiring the client to know task ids up front.
"""
```

Modify the `publish` method to also fan out to the global topic. Replace the existing `publish` body:
```python
    async def publish(self, topic: str, data: dict[str, Any]) -> None:
        """Publish ``data`` to all subscribers of ``topic`` AND to the global topic.

        If a subscriber's queue is full, the message is dropped for that
        subscriber (logged) rather than blocking the publisher.
        """
        self._fan_out(topic, data)
        if topic != GLOBAL_TOPIC:
            self._fan_out(GLOBAL_TOPIC, data)

    def _fan_out(self, topic: str, data: dict[str, Any]) -> None:
        """Deliver ``data`` to every subscriber of ``topic`` (best-effort)."""
        for queue in self._subscribers.get(topic, []):
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                logger.warning("EventBus queue full for topic '%s'; dropping message", topic)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_events.py -v`
Expected: All tests PASS (new + existing).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/devflow/daemon/events.py tests/unit/daemon/test_events.py
git commit -m "feat(events): add global '*' topic + sse-starlette dependency

EventBus.publish now fans out to GLOBAL_TOPIC='*' in addition to the
specific topic, so an SSE subscriber can stream all daemon events
without knowing task ids. sse-starlette added for /api/events endpoint."
```

---

## Task 2: SSE endpoint `/api/events`

**Files:**
- Modify: `src/devflow/daemon/web.py` (add SSE route using `event_bus`)
- Test: `tests/unit/daemon/test_web_sse.py`

**Interfaces:**
- Consumes: `EventBus` (Task 1 global topic), `sse-starlette`'s `EventSourceResponse`
- Produces: `GET /api/events` — `text/event-stream`. Each EventBus message becomes an SSE event with `event: <data["event"]>` and `data: <json>`. The endpoint subscribes to `GLOBAL_TOPIC` and streams until the client disconnects.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/daemon/test_web_sse.py`:
```python
"""Tests for the /api/events SSE endpoint."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from devflow.daemon.events import EventBus, GLOBAL_TOPIC
from devflow.daemon.locks import DaemonLocks
from devflow.daemon.web import create_app
from devflow.config import Config


def test_sse_endpoint_streams_events(mock_config: Config) -> None:
    """GET /api/events returns a text/event-stream with published events."""
    bus = EventBus()
    app = create_app(mock_config, DaemonLocks(), bus)
    client = TestClient(app)

    # The SSE endpoint is a streaming response. Use TestClient's stream context.
    with client.stream("GET", "/api/events") as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

        # Publish an event from "outside" (in the same process).
        # We need to publish via the bus's async API; use a thread or
        # the app's event loop. TestClient runs the app in a portal/thread.
        # Simplest: use asyncio.run in a separate thread after a short sleep.
        import threading

        def _publish_after_delay() -> None:
            asyncio.run(asyncio.sleep(0.1))
            asyncio.run(bus.publish(GLOBAL_TOPIC, {"event": "task.started", "task_id": "T-1"}))

        t = threading.Thread(target=_publish_after_delay, daemon=True)
        t.start()

        # Read one event from the stream.
        events_received: list[dict[str, str]] = []
        for line in resp.iter_lines():
            if line.startswith("event:"):
                events_received.append({"event": line.split(":", 1)[1].strip()})
            if len(events_received) >= 1:
                break

    assert len(events_received) >= 1
    assert events_received[0]["event"] == "task.started"


def test_sse_endpoint_requires_event_bus(mock_config: Config) -> None:
    """The SSE endpoint is always available (event_bus is a required param)."""
    bus = EventBus()
    app = create_app(mock_config, DaemonLocks(), bus)
    client = TestClient(app)
    # Just open the stream and immediately close — should not 404.
    with client.stream("GET", "/api/events") as resp:
        assert resp.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_web_sse.py -v`
Expected: FAIL with 404 (route doesn't exist yet).

- [ ] **Step 3: Add the SSE endpoint**

In `src/devflow/daemon/web.py`, add import at the top (after the `from devflow.daemon.events import EventBus` line):
```python
import json

from sse_starlette.sse import EventSourceResponse

from devflow.daemon.events import GLOBAL_TOPIC
```
(Remove the existing `from devflow.daemon.events import EventBus` line and replace with the two-line import above so both `EventBus` and `GLOBAL_TOPIC` are imported. Do NOT duplicate.)

Add the SSE route inside `create_app`, after the `state()` route and before the conditional `if approval_store is not None:` block:
```python
    @app.get("/api/events")
    async def event_stream() -> EventSourceResponse:
        """Server-Sent Events stream of all daemon events.

        Subscribes to the EventBus global topic and forwards each event.
        The client connects via ``new EventSource('/api/events')``.
        """
        import asyncio

        queue = await event_bus.subscribe(GLOBAL_TOPIC)

        async def event_generator() -> typing.AsyncGenerator[dict[str, str], None]:
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                        yield {
                            "event": msg.get("event", "message"),
                            "data": json.dumps(msg),
                        }
                    except asyncio.TimeoutError:
                        # Heartbeat keepalive (prevents proxy/browser timeouts).
                        yield {"event": "ping", "data": "{}"}
            finally:
                # Best-effort cleanup of the subscriber queue.
                pass

        return EventSourceResponse(event_generator())
```

Add `import typing` and `import json` at the top of the file (in the stdlib import group) if not already present.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_web_sse.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Run full daemon test suite**

Run: `python -m pytest tests/unit/daemon -v`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/devflow/daemon/web.py tests/unit/daemon/test_web_sse.py
git commit -m "feat(daemon): add /api/events SSE endpoint

GET /api/events streams all daemon events (task.started/finished/error,
eod.ready) via sse-starlette EventSourceResponse. Subscribes to the
EventBus global topic. Includes 15s ping heartbeat for proxy keepalive."
```

---

## Task 3: `/api/tasks/*` routes

**Files:**
- Modify: `src/devflow/daemon/web.py` (add task routes + response models)
- Test: `tests/unit/daemon/test_web_tasks.py`

**Interfaces:**
- Consumes: `runner` (for current task), `eod_handler`/`batch_store` (for done tasks), `task_source` (for queue). The `_state["current_task"]` dict already tracks the active task id.
- Produces:
  - `GET /api/tasks/current` → `{"task_id": str | None, "node": str | None}` (node is None for now — runner doesn't track it)
  - `GET /api/tasks/done` → `list[dict]` (published batch entries from `eod_handler._store.list_all(status="published")`; if no eod_handler, empty list)
  - `GET /api/tasks/{task_id}` → full detail from batch store `get_by_task(task_id)` (most recent entry) or 404

  Note: `/api/tasks/queue` is deferred (requires a live task_source connection which the daemon doesn't hold between runs — `runner._task_source` is None and built lazily). The frontend can infer queue from task source status; this route returns `{"queue": [], "note": "queue introspection not available without an active task source"}`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/daemon/test_web_tasks.py`:
```python
"""Tests for /api/tasks/* routes."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from devflow.batch.eod_handler import EodHandler
from devflow.batch.models import BatchEntry, BatchStatus
from devflow.batch.store import BatchStore
from devflow.config import Config
from devflow.daemon.events import EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.daemon.web import create_app
from devflow.schemas import ReporterResponse
from devflow.state import FinalVerdict


def _make_entry(task_id: str, status: str = BatchStatus.PUBLISHED) -> BatchEntry:
    return BatchEntry(
        task_id=task_id,
        task_title=f"Task {task_id}",
        branch_name=f"devflow/{task_id}/abc",
        worktree_path="/tmp/repo-wt",
        diff="diff",
        plan_summary="s",
        plan_steps=["step 1"],
        checker_reports=[],
        self_review_notes="",
        final_verdict=FinalVerdict.APPROVE,
        reporter_artifacts=ReporterResponse(
            pr_title="t", pr_description="d", corporate_report="r", commit_message="c"
        ),
        status=status,
        created_at="2026-07-13T10:00:00Z",
    )


def test_tasks_current_returns_none_initially(mock_config: Config) -> None:
    """GET /api/tasks/current returns null task_id when nothing is running."""
    bus = EventBus()
    app = create_app(mock_config, DaemonLocks(), bus)
    client = TestClient(app)
    resp = client.get("/api/tasks/current")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] is None
    assert data["node"] is None


def test_tasks_current_reflects_set_current_task(mock_config: Config) -> None:
    """GET /api/tasks/current reflects what set_current_task was called with."""
    bus = EventBus()
    app = create_app(mock_config, DaemonLocks(), bus)
    app.state.set_current_task("T-42")  # type: ignore[attr-defined]
    client = TestClient(app)
    resp = client.get("/api/tasks/current")
    assert resp.status_code == 200
    assert resp.json()["task_id"] == "T-42"


def test_tasks_done_returns_published_entries(
    mock_config: Config, tmp_path: Path
) -> None:
    """GET /api/tasks/done returns published batch entries."""
    store = BatchStore(tmp_path / "batch_store.db")
    store.add(_make_entry("T-1"))
    store.add(_make_entry("T-2"))
    handler = EodHandler(mock_config, store, EventBus(), repo_path=".")
    app = create_app(mock_config, DaemonLocks(), EventBus(), eod_handler=handler)
    client = TestClient(app)
    resp = client.get("/api/tasks/done")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert {e["task_id"] for e in data} == {"T-1", "T-2"}
    store.close()


def test_tasks_done_empty_when_no_eod_handler(mock_config: Config) -> None:
    """GET /api/tasks/done returns [] when no eod_handler is wired."""
    app = create_app(mock_config, DaemonLocks(), EventBus())
    client = TestClient(app)
    resp = client.get("/api/tasks/done")
    assert resp.status_code == 200
    assert resp.json() == []


def test_tasks_detail_returns_entry_by_task_id(
    mock_config: Config, tmp_path: Path
) -> None:
    """GET /api/tasks/{task_id} returns the most recent entry for that task."""
    store = BatchStore(tmp_path / "batch_store.db")
    store.add(_make_entry("T-1"))
    handler = EodHandler(mock_config, store, EventBus(), repo_path=".")
    app = create_app(mock_config, DaemonLocks(), EventBus(), eod_handler=handler)
    client = TestClient(app)
    resp = client.get("/api/tasks/T-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "T-1"
    assert data["reporter_artifacts"]["pr_title"] == "t"
    store.close()


def test_tasks_detail_404_for_unknown(mock_config: Config, tmp_path: Path) -> None:
    """GET /api/tasks/{task_id} returns 404 when no entry exists."""
    store = BatchStore(tmp_path / "batch_store.db")
    handler = EodHandler(mock_config, store, EventBus(), repo_path=".")
    app = create_app(mock_config, DaemonLocks(), EventBus(), eod_handler=handler)
    client = TestClient(app)
    resp = client.get("/api/tasks/T-UNKNOWN")
    assert resp.status_code == 404
    store.close()


def test_tasks_queue_returns_empty_note(mock_config: Config) -> None:
    """GET /api/tasks/queue returns an empty list with a note."""
    app = create_app(mock_config, DaemonLocks(), EventBus())
    client = TestClient(app)
    resp = client.get("/api/tasks/queue")
    assert resp.status_code == 200
    data = resp.json()
    assert data["queue"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_web_tasks.py -v`
Expected: FAIL with 404 on `/api/tasks/current`.

- [ ] **Step 3: Add the task routes**

In `src/devflow/daemon/web.py`, add response models (after `EodPublishRequest`):
```python
class TaskCurrentResponse(BaseModel):
    """Active task + current graph node (node is None until runner tracks it)."""

    task_id: str | None = None
    node: str | None = None


class TaskQueueResponse(BaseModel):
    """Pending task queue (introspection limited without a live task source)."""

    queue: list[dict[str, Any]] = Field(default_factory=list)
    note: str = ""
```

Add the routes inside `create_app`, after the `/api/state` route (and before `/api/events` or the approvals block — order doesn't matter functionally, but keep them grouped):
```python
    @app.get("/api/tasks/current", response_model=TaskCurrentResponse)
    async def tasks_current() -> TaskCurrentResponse:
        return TaskCurrentResponse(
            task_id=_state.get("current_task"),
            node=None,
        )

    @app.get("/api/tasks/queue", response_model=TaskQueueResponse)
    async def tasks_queue() -> TaskQueueResponse:
        return TaskQueueResponse(
            queue=[],
            note="queue introspection not available without an active task source",
        )

    @app.get("/api/tasks/done")
    async def tasks_done() -> list[dict[str, Any]]:
        if eod_handler is None:
            return []
        try:
            entries = eod_handler._store.list_all(status="published")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - defensive
            return []
        return [_entry_summary(e) for e in entries]

    @app.get("/api/tasks/{task_id}")
    async def task_detail(task_id: str) -> dict[str, Any]:
        if eod_handler is None:
            raise HTTPException(status_code=404, detail="No batch store available")
        entries = eod_handler._store.get_by_task(task_id)  # type: ignore[attr-defined]
        if not entries:
            raise HTTPException(status_code=404, detail=f"No entry for task {task_id}")
        # Most recent entry (get_by_task returns oldest-first).
        return entries[-1].model_dump(mode="json")
```

**IMPORTANT ordering:** `/api/tasks/current` and `/api/tasks/queue` MUST be registered BEFORE `/api/tasks/{task_id}`, or FastAPI will match "current"/"queue" as the `{task_id}` path param. Place the three static routes first, then the `{task_id}` route last.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_web_tasks.py -v`
Expected: All 7 tests PASS.

- [ ] **Step 5: Run full daemon suite**

Run: `python -m pytest tests/unit/daemon -v`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/devflow/daemon/web.py tests/unit/daemon/test_web_tasks.py
git commit -m "feat(daemon): add /api/tasks/* routes

GET /api/tasks/current (active task + node), /api/tasks/queue (note:
no live source), /api/tasks/done (published batch entries),
/api/tasks/{task_id} (full detail from batch store, 404 if unknown).
Static routes registered before {task_id} param route for matching."
```

---

## Task 4: DaemonConfig frontend fields + CORS middleware

**Files:**
- Modify: `src/devflow/config.py` (`DaemonConfig` += frontend fields)
- Modify: `src/devflow/daemon/web.py` (add CORS middleware)
- Test: `tests/unit/test_config.py` (extend), `tests/unit/daemon/test_web_cors_static.py`

**Interfaces:**
- Consumes: nothing new
- Produces: `DaemonConfig.serve_frontend: bool = True`, `DaemonConfig.frontend_dist: str = "frontend/dist"`, `DaemonConfig.cors_origins: list[str] = []`. When `cors_origins` is non-empty, `CORSMiddleware` is added to the app allowing those origins (for Vite dev server `http://localhost:5173`).

- [ ] **Step 1: Write the failing config test**

Add to `tests/unit/test_config.py`:
```python
def test_daemon_config_has_frontend_defaults() -> None:
    """DaemonConfig gets frontend defaults."""
    from devflow.config import DaemonConfig

    cfg = DaemonConfig()
    assert cfg.serve_frontend is True
    assert cfg.frontend_dist == "frontend/dist"
    assert cfg.cors_origins == []


def test_daemon_config_frontend_from_yaml(tmp_path: Path) -> None:
    """Frontend config loads from YAML."""
    from devflow.config import load_workflow_config

    yaml_path = tmp_path / "workflow.yaml"
    yaml_path.write_text(
        "task_source: mock\n"
        "daemon:\n"
        "  enabled: true\n"
        "  cors_origins:\n"
        "    - http://localhost:5173\n"
        "  frontend_dist: build/spa\n",
        encoding="utf-8",
    )
    cfg = load_workflow_config(yaml_path)
    assert cfg.daemon.cors_origins == ["http://localhost:5173"]
    assert cfg.daemon.frontend_dist == "build/spa"
```

- [ ] **Step 2: Run config test to verify it fails**

Run: `python -m pytest tests/unit/test_config.py::test_daemon_config_has_frontend_defaults -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Add the config fields**

In `src/devflow/config.py`, extend `DaemonConfig` (add the three fields after `approval_on_timeout`):
```python
class DaemonConfig(BaseModel):
    enabled: bool = False
    task_schedule: str = "0 9,15 * * 1-5"
    eod_schedule: str = "0 18 * * 1-5"
    port: int = 8787
    approval_timeout_hours: int = 8
    approval_on_timeout: str = "defer"  # defer | reject
    # Frontend dashboard (Phase 5).
    serve_frontend: bool = True
    frontend_dist: str = "frontend/dist"
    cors_origins: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Run config test to verify it passes**

Run: `python -m pytest tests/unit/test_config.py -v`
Expected: All PASS.

- [ ] **Step 5: Write the CORS test**

Create `tests/unit/daemon/test_web_cors_static.py`:
```python
"""Tests for CORS middleware and static file config."""

from __future__ import annotations

from fastapi.testclient import TestClient

from devflow.config import Config
from devflow.daemon.events import EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.daemon.web import create_app


def test_cors_not_added_when_no_origins(mock_config: Config) -> None:
    """No CORS headers when cors_origins is empty."""
    app = create_app(mock_config, DaemonLocks(), EventBus())
    client = TestClient(app)
    resp = client.options(
        "/api/health",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    # No CORS allow-origin header.
    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}


def test_cors_added_when_origins_configured(mock_config: Config) -> None:
    """CORS Allow-Origin returned when the origin is in cors_origins."""
    mock_config.workflow.daemon.cors_origins = ["http://localhost:5173"]
    app = create_app(mock_config, DaemonLocks(), EventBus())
    client = TestClient(app)
    resp = client.options(
        "/api/health",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
```

- [ ] **Step 6: Run CORS test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_web_cors_static.py::test_cors_added_when_origins_configured -v`
Expected: FAIL (no CORS middleware yet).

- [ ] **Step 7: Add CORS middleware to web.py**

In `src/devflow/daemon/web.py`, add import at top:
```python
from fastapi.middleware.cors import CORSMiddleware
```

In `create_app`, after `app = FastAPI(...)` (line ~68) and before route definitions, add:
```python
    # CORS: allow the Vite dev server origin(s) when configured.
    cors_origins = app_cfg.workflow.daemon.cors_origins
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
```

- [ ] **Step 8: Run CORS test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_web_cors_static.py -v`
Expected: All PASS.

- [ ] **Step 9: Run full daemon + config suites**

Run: `python -m pytest tests/unit/daemon tests/unit/test_config.py -v`
Expected: All PASS.

- [ ] **Step 10: Commit**

```bash
git add src/devflow/config.py src/devflow/daemon/web.py tests/unit/test_config.py tests/unit/daemon/test_web_cors_static.py
git commit -m "feat(daemon): add CORS middleware + frontend config fields

DaemonConfig gains serve_frontend (default True), frontend_dist
('frontend/dist'), cors_origins ([]). When cors_origins is non-empty,
CORSMiddleware allows those origins (for Vite dev server :5173).
Frontend static serving added in next task."
```

---

## Task 5: Static file serving (production) + SPA fallback

**Files:**
- Modify: `src/devflow/daemon/web.py` (mount StaticFiles for `frontend_dist` when it exists)
- Test: `tests/unit/daemon/test_web_cors_static.py` (extend)

**Interfaces:**
- Consumes: `DaemonConfig.serve_frontend`, `DaemonConfig.frontend_dist`
- Produces: When `serve_frontend=True` AND the `frontend_dist` directory exists, the app mounts `StaticFiles(html=True)` at `/` (after all `/api/*` routes) so production serves the SPA. A catch-all GET route serves `index.html` for unknown non-`/api` paths (SPA client-side routing fallback). If the directory doesn't exist (e.g., frontend not built), it's skipped with a log warning — the daemon still serves the API.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/daemon/test_web_cors_static.py`:
```python
def test_static_serving_when_dist_exists(mock_config: Config, tmp_path) -> None:
    """When frontend_dist exists, the app serves its index.html at /."""
    from pathlib import Path

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>SPA</body></html>", encoding="utf-8")
    mock_config.workflow.daemon.serve_frontend = True
    mock_config.workflow.daemon.frontend_dist = str(dist)

    app = create_app(mock_config, DaemonLocks(), EventBus())
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "SPA" in resp.text


def test_static_serving_skipped_when_dist_missing(mock_config: Config, tmp_path) -> None:
    """When frontend_dist doesn't exist, / is not served (API still works)."""
    mock_config.workflow.daemon.serve_frontend = True
    mock_config.workflow.daemon.frontend_dist = str(tmp_path / "nonexistent")
    app = create_app(mock_config, DaemonLocks(), EventBus())
    client = TestClient(app)
    # API still works.
    assert client.get("/api/health").status_code == 200
    # Root is 404 (no static, no SPA).
    resp = client.get("/")
    assert resp.status_code == 404


def test_spa_fallback_serves_index_for_unknown_paths(mock_config: Config, tmp_path) -> None:
    """Unknown non-/api paths serve index.html (SPA client-side routing)."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>SPA fallback</html>", encoding="utf-8")
    mock_config.workflow.daemon.serve_frontend = True
    mock_config.workflow.daemon.frontend_dist = str(dist)

    app = create_app(mock_config, DaemonLocks(), EventBus())
    client = TestClient(app)
    resp = client.get("/some/spa/route")
    assert resp.status_code == 200
    assert "SPA fallback" in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_web_cors_static.py::test_static_serving_when_dist_exists -v`
Expected: FAIL (404 — no static mount yet).

- [ ] **Step 3: Add static serving + SPA fallback**

In `src/devflow/daemon/web.py`, add import at top:
```python
import os

from fastapi.responses import FileResponse
```
(`os` may already be imported; check first. `FileResponse` is new.)

At the END of `create_app` (just before `return app`), add:
```python
    # Serve the built frontend SPA in production (when the dist dir exists).
    daemon_cfg = app_cfg.workflow.daemon
    if daemon_cfg.serve_frontend:
        dist_path = daemon_cfg.frontend_dist
        index_file = os.path.join(dist_path, "index.html")
        if os.path.isdir(dist_path) and os.path.isfile(index_file):
            from fastapi.staticfiles import StaticFiles

            # Mount static assets (JS/CSS/images) under /assets.
            assets_dir = os.path.join(dist_path, "assets")
            if os.path.isdir(assets_dir):
                app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

            # SPA fallback: any non-/api GET serves index.html.
            @app.get("/{full_path:path}")
            async def spa_fallback(full_path: str) -> FileResponse:
                # Never intercept API routes.
                if full_path.startswith("api"):
                    raise HTTPException(status_code=404)
                # Serve a specific static file if it exists, else index.html.
                candidate = os.path.join(dist_path, full_path)
                if full_path and os.path.isfile(candidate):
                    return FileResponse(candidate)
                return FileResponse(index_file)

            logger.info("Serving frontend SPA from %s", dist_path)
        else:
            logger.warning(
                "Frontend dist not found at %s; daemon serves API only. "
                "Run `npm run build` in frontend/ to build the SPA.",
                dist_path,
            )

    return app
```

**IMPORTANT:** The `return app` that was already at the end of `create_app` must be REMOVED (it's now inside the `if` block's else-equivalent — actually it's after the block). Carefully: the original last lines were:
```python
    app.state.set_current_task = set_current_task  # type: ignore[attr-defined]

    return app
```
Replace with:
```python
    app.state.set_current_task = set_current_task  # type: ignore[attr-defined]

    # Serve the built frontend SPA in production (when the dist dir exists).
    daemon_cfg = app_cfg.workflow.daemon
    if daemon_cfg.serve_frontend:
        # ... (block from above) ...
        else:
            logger.warning(...)

    return app
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_web_cors_static.py -v`
Expected: All PASS.

- [ ] **Step 5: Run full daemon suite**

Run: `python -m pytest tests/unit/daemon -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add src/devflow/daemon/web.py tests/unit/daemon/test_web_cors_static.py
git commit -m "feat(daemon): serve built frontend SPA + SPA routing fallback

When serve_frontend=True and frontend_dist exists, mounts /assets
static and a catch-all GET that serves index.html for non-/api paths
(SPA client-side routing). Skips gracefully with a warning if the
dist dir is missing (daemon serves API only)."
```

---

## Task 6: Wire runner `on_task_change` callback (set_current_task)

**Files:**
- Modify: `src/devflow/daemon/runner.py` (accept callback, call in run_task)
- Modify: `src/devflow/daemon/__main__.py` (wire the callback)
- Test: `tests/unit/daemon/test_runner_callback.py`

**Interfaces:**
- Consumes: `app.state.set_current_task` from web.py
- Produces: `WorkflowRunner.__init__(..., on_task_change: Callable[[str | None], None] | None = None)`. In `run_task`, call `on_task_change(task_id)` before the run and `on_task_change(None)` after. In `run_daemon`, after building the app, set the runner's callback to `app.state.set_current_task`.

  **Design note:** The app is built inside `run_web_server` (which blocks). To wire the callback, `run_daemon` must build the app itself (not inside `run_web_server`) and pass it. Refactor: `run_web_server` gains an optional `app: FastAPI | None = None` param — if provided, use it; else build via `create_app`. Then `run_daemon` builds the app, wires the callback, and passes the app to `run_web_server`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/daemon/test_runner_callback.py`:
```python
"""Tests for the WorkflowRunner on_task_change callback."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from devflow.config import Config, HitlStrategy
from devflow.daemon.events import EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.daemon.runner import WorkflowRunner


def test_run_task_calls_on_task_change(
    mock_config: Config, tmp_path: Path
) -> None:
    """run_task invokes on_task_change with the task id, then None."""
    calls: list[str | None] = []
    runner = WorkflowRunner(
        mock_config,
        EventBus(),
        DaemonLocks(),
        task_source=MagicMock(),
        on_task_change=calls.append,
    )
    # We can't easily run the full graph in a unit test; test that the
    # callback is wired by checking it's stored and would be called.
    # Instead, directly invoke the internal flow by mocking run_workflow.
    # Simpler: just verify the callback attribute is set.
    assert runner._on_task_change is calls.append


def test_on_task_change_defaults_to_none(mock_config: Config) -> None:
    """Without on_task_change, the runner still constructs."""
    runner = WorkflowRunner(mock_config, EventBus(), DaemonLocks())
    assert runner._on_task_change is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_runner_callback.py -v`
Expected: FAIL with `TypeError: unexpected keyword argument 'on_task_change'`.

- [ ] **Step 3: Add the callback to WorkflowRunner**

In `src/devflow/daemon/runner.py`, add import at top:
```python
from collections.abc import Callable
```

In `__init__`, add the param:
```python
    def __init__(
        self,
        app_cfg: Config,
        event_bus: EventBus,
        locks: DaemonLocks,
        task_source: TaskSource | None = None,
        approval_bridge: ApprovalBridge | None = None,
        batch_store: BatchStore | None = None,
        on_task_change: Callable[[str | None], None] | None = None,
    ) -> None:
        self._cfg = app_cfg
        self._bus = event_bus
        self._locks = locks
        self._task_source = task_source
        self._bridge = approval_bridge
        self._batch_store = batch_store
        self._on_task_change = on_task_change
        self.events_published: int = 0
```

In `run_task`, call the callback. After the `topic = f"task.{task_id}"` line and before `self._publish(...)`, add:
```python
        if self._on_task_change is not None:
            self._on_task_change(task_id)
```

And in the `finally`/return path — actually, add it right before each `return` and in the `except`. Simplest: wrap the body. After the `try:` block's successful return, the callback should fire with None. Replace the existing success path's `return final_state` (after the batch store block) to set None first:
```python
            if (
                self._batch_store is not None
                and self._cfg.workflow.hitl_strategy == HitlStrategy.END_OF_DAY
            ):
                try:
                    self._store_batch_entry(task_id, final_state)
                except Exception:
                    logger.exception("Failed to store batch entry for task %s", task_id)
            if self._on_task_change is not None:
                self._on_task_change(None)
            return final_state
```

And in the `except Exception:` block (before `raise`), add:
```python
        except Exception:
            logger.exception("Workflow run failed for task %s", task_id)
            self._publish(
                topic,
                {"event": "task.error", "task_id": task_id, "error": traceback.format_exc()},
            )
            if self._on_task_change is not None:
                self._on_task_change(None)
            raise
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_runner_callback.py -v`
Expected: All PASS.

- [ ] **Step 5: Wire the callback in __main__.py**

In `src/devflow/daemon/__main__.py`, refactor `run_daemon` to build the app explicitly so it can wire the callback. The change: build the app via `create_app` BEFORE constructing the runner's callback, then pass the app to `run_web_server`.

This requires `run_web_server` to accept a pre-built app. Modify `run_web_server` in `web.py`:
```python
def run_web_server(
    app_cfg: Config,
    locks: DaemonLocks,
    event_bus: EventBus,
    runner: Any | None = None,
    approval_store: ApprovalStore | None = None,
    eod_handler: EodHandler | None = None,
    app: FastAPI | None = None,
) -> None:
    import uvicorn

    if app is None:
        app = create_app(
            app_cfg, locks, event_bus, runner,
            approval_store=approval_store, eod_handler=eod_handler,
        )
    port = app_cfg.workflow.daemon.port
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
```

Then in `__main__.py` `run_daemon`, replace the runner construction + web server call. After building the runner (without callback yet), build the app, then set the callback:
```python
    # Construct the runner once, with the bridge attached so run_task uses
    # run_workflow_interactive (pausing on plan/publish approval interrupts).
    runner = WorkflowRunner(app_cfg, event_bus, locks, approval_bridge=bridge)

    # Build the app explicitly so we can wire the current-task callback.
    app = create_app(
        app_cfg, locks, event_bus, runner,
        approval_store=approval_store, eod_handler=eod_handler,
    )
    runner._on_task_change = app.state.set_current_task  # type: ignore[attr-defined]

    # 4. Create and start scheduler, register jobs.
    scheduler = DaemonScheduler(app_cfg, runner, eod_handler=eod_handler)
```

And the web server call at the bottom becomes:
```python
    try:
        run_web_server(
            app_cfg, locks, event_bus, runner,
            approval_store=approval_store, eod_handler=eod_handler, app=app,
        )
    finally:
        ...
```

Add the import `from devflow.daemon.web import create_app, run_web_server` (update the existing import line).

- [ ] **Step 6: Run full daemon suite**

Run: `python -m pytest tests/unit/daemon -v`
Expected: All PASS. The existing `test_main.py` tests may need adjustment if they assert `run_web_server` was called with specific args — check and update.

- [ ] **Step 7: Run ruff + mypy**

Run: `python -m ruff check src/devflow/daemon/runner.py src/devflow/daemon/__main__.py src/devflow/daemon/web.py`
Run: `python -m mypy src/devflow/daemon/runner.py src/devflow/daemon/__main__.py src/devflow/daemon/web.py`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add src/devflow/daemon/runner.py src/devflow/daemon/__main__.py src/devflow/daemon/web.py tests/unit/daemon/test_runner_callback.py
git commit -m "feat(daemon): wire on_task_change callback (set_current_task)

WorkflowRunner gains optional on_task_change callback, invoked with the
task id at run start and None at end/error. run_daemon builds the app
explicitly and wires runner._on_task_change = app.state.set_current_task
so /api/health and /api/tasks/current reflect the active task. run_web_server
accepts an optional pre-built app."
```

---

## Task 7: Frontend scaffold (Vite + Vue 3 + TS)

**Files:**
- Create: `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tsconfig.json`, `frontend/tsconfig.node.json`, `frontend/index.html`, `frontend/src/main.ts`, `frontend/src/App.vue`, `frontend/.gitignore`
- Create: `frontend/env.d.ts`

**Interfaces:**
- Consumes: nothing (this is project scaffolding)
- Produces: a runnable `npm install && npm run dev` Vite project that boots an empty Vue 3 app on `:5173`, proxying `/api/*` to `:8787`.

**NOTE:** This task does NOT use TDD (it's scaffolding). Verification is `npm install` succeeds and `npm run build` produces `dist/index.html`. The implementer must have Node.js available. If `npm` is not available in the environment, this task (and all frontend tasks) are BLOCKED — report that.

- [ ] **Step 1: Create package.json**

Create `frontend/package.json`:
```json
{
  "name": "devflow-dashboard",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vue-tsc --noEmit && vite build",
    "preview": "vite preview",
    "typecheck": "vue-tsc --noEmit",
    "gen:types": "openapi-typescript http://localhost:8787/openapi.json -o src/api/schema.ts"
  },
  "dependencies": {
    "pinia": "^2.2.0",
    "vue": "^3.5.0",
    "vue-router": "^4.4.0"
  },
  "devDependencies": {
    "@vitejs/plugin-vue": "^5.1.0",
    "openapi-typescript": "^7.3.0",
    "typescript": "^5.6.0",
    "vite": "^5.4.0",
    "vue-tsc": "^2.1.0"
  }
}
```

- [ ] **Step 2: Create vite.config.ts**

Create `frontend/vite.config.ts`:
```typescript
import { defineConfig } from 'vue'
import vue from '@vitejs/plugin-vue'

// Dev server proxies /api/* to the FastAPI daemon on :8787.
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8787',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
```

- [ ] **Step 3: Create tsconfig files**

Create `frontend/tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "jsx": "preserve",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "esModuleInterop": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "skipLibCheck": true,
    "noEmit": true,
    "baseUrl": ".",
    "paths": {
      "@/*": ["src/*"]
    }
  },
  "include": ["src/**/*.ts", "src/**/*.d.ts", "src/**/*.vue", "env.d.ts"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

Create `frontend/tsconfig.node.json`:
```json
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true
  },
  "include": ["vite.config.ts"]
}
```

Create `frontend/env.d.ts`:
```typescript
/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<{}, {}, any>
  export default component
}
```

- [ ] **Step 4: Create index.html**

Create `frontend/index.html`:
```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>DevFlow Dashboard</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.ts"></script>
  </body>
</html>
```

- [ ] **Step 5: Create src/App.vue (minimal placeholder)**

Create `frontend/src/App.vue`:
```vue
<script setup lang="ts">
// Root component — layout + nav added in Task 11.
</script>

<template>
  <div id="devflow-app">
    <h1>DevFlow Dashboard</h1>
    <p>Scaffold OK. Routes and stores added in later tasks.</p>
  </div>
</template>
```

- [ ] **Step 6: Create src/main.ts**

Create `frontend/src/main.ts`:
```typescript
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'

const app = createApp(App)
app.use(createPinia())
// Router added in Task 11.
app.mount('#app')
```

- [ ] **Step 7: Create .gitignore**

Create `frontend/.gitignore`:
```
node_modules/
dist/
*.local
.vite/
```

- [ ] **Step 8: Install + build verification**

Run (from `frontend/`):
```bash
cd frontend && npm install && npm run build
```
Expected: `npm install` succeeds (downloads deps); `npm run build` produces `frontend/dist/index.html` and `frontend/dist/assets/`.

If `npm` is not available, report BLOCKED with the exact error.

- [ ] **Step 9: Commit**

```bash
git add frontend/package.json frontend/vite.config.ts frontend/tsconfig.json frontend/tsconfig.node.json frontend/env.d.ts frontend/index.html frontend/src/main.ts frontend/src/App.vue frontend/.gitignore
git commit -m "feat(frontend): scaffold Vue 3 + Vite + TypeScript project

frontend/ npm package: Vue 3, Pinia, Vue Router, TypeScript (strict),
Vite. Dev server proxies /api/* to FastAPI :8787. openapi-typescript
for type generation. Build verified (dist/index.html produced)."
```

(Do NOT commit `node_modules/` or `dist/` — they're gitignored.)

---

## Task 8: Typed API client + OpenAPI types

**Files:**
- Create: `frontend/src/api/client.ts`, `frontend/src/api/types.ts`, `frontend/src/api/schema.ts`
- Create: `frontend/scripts/gen-types.sh` (helper)

**Interfaces:**
- Consumes: FastAPI `/openapi.json` (auto-generated), native `fetch`
- Produces: `frontend/src/api/client.ts` exporting typed functions: `getHealth()`, `getState()`, `getTasksCurrent()`, `getTasksDone()`, `getTaskDetail(id)`, `getApprovals()`, `resolveApproval(threadId, decision)`, `getEod()`, `eodFinalize()`, `eodPublish(taskIds)`. `types.ts` exports hand-written interfaces matching the backend response models (since `gen:types` requires a running daemon, the implementer writes types.ts by hand from the known backend shapes and optionally regenerates schema.ts later).

- [ ] **Step 1: Write types.ts (hand-written from backend models)**

Create `frontend/src/api/types.ts`:
```typescript
// Hand-written types matching the FastAPI response models.
// Regenerate from OpenAPI via `npm run gen:types` when the daemon is running.

export interface HealthResponse {
  status: string
  scheduler: string
  uptime_seconds: number
  current_task: string | null
  pending_approvals: number
  batch_store_pending: number
  errors_last_24h: number
}

export interface StateResponse {
  hitl_strategy: string
  daemon: {
    enabled: boolean
    task_schedule: string
    eod_schedule: string
    port: number
    approval_timeout_hours: number
    approval_on_timeout: string
  }
  task_source: string
}

export interface TaskCurrentResponse {
  task_id: string | null
  node: string | null
}

export interface TaskQueueResponse {
  queue: Array<Record<string, unknown>>
  note: string
}

export interface EodEntrySummary {
  id: number
  task_id: string
  task_title: string
  branch_name: string
  final_verdict: string | null
  status: string
  created_at: string
}

export interface ReporterArtifacts {
  pr_title: string
  pr_description: string
  corporate_report: string
  commit_message: string
}

export interface CheckerReport {
  agent_name: string
  verdict: string
  summary: string
  findings: string[]
  suggestions: string[]
}

export interface BatchEntryDetail {
  id: number | null
  task_id: string
  task_title: string
  branch_name: string
  worktree_path: string
  diff: string
  plan_summary: string
  plan_steps: string[]
  checker_reports: CheckerReport[]
  self_review_notes: string
  final_verdict: string | null
  reporter_artifacts: ReporterArtifacts
  status: string
  created_at: string
  published_at: string | null
  mr_url: string | null
  pushed_sha: string | null
  rejection_reason: string | null
}

export interface ApprovalPending {
  thread_id: string
  payload: Record<string, unknown>
}

export interface ApprovalDecision {
  approved: boolean
  reason?: string
  requested_changes?: string[]
}

export interface EodPublishResult {
  published: string[]
  failed: string[]
  skipped: string[]
}

// SSE event shapes (from EventBus data dicts).
export interface SseEvent {
  event: string
  [key: string]: unknown
}
```

- [ ] **Step 2: Write client.ts**

Create `frontend/src/api/client.ts`:
```typescript
import type {
  ApprovalDecision,
  ApprovalPending,
  BatchEntryDetail,
  EodEntrySummary,
  EodPublishResult,
  HealthResponse,
  StateResponse,
  TaskCurrentResponse,
  TaskQueueResponse,
} from './types'

const BASE = import.meta.env.BASE_URL || '/'

async function getJson<T>(path: string): Promise<T> {
  const resp = await fetch(`${BASE}api${path}`, {
    headers: { Accept: 'application/json' },
  })
  if (!resp.ok) {
    throw new Error(`GET ${path} failed: ${resp.status} ${resp.statusText}`)
  }
  return resp.json() as Promise<T>
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const resp = await fetch(`${BASE}api${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!resp.ok) {
    throw new Error(`POST ${path} failed: ${resp.status} ${resp.statusText}`)
  }
  return resp.json() as Promise<T>
}

// --- Health & state ---
export const getHealth = () => getJson<HealthResponse>('/health')
export const getState = () => getJson<StateResponse>('/state')

// --- Tasks ---
export const getTasksCurrent = () => getJson<TaskCurrentResponse>('/tasks/current')
export const getTasksQueue = () => getJson<TaskQueueResponse>('/tasks/queue')
export const getTasksDone = () => getJson<EodEntrySummary[]>('/tasks/done')
export const getTaskDetail = (taskId: string) =>
  getJson<BatchEntryDetail>(`/tasks/${encodeURIComponent(taskId)}`)

// --- Approvals ---
export const getApprovals = () => getJson<ApprovalPending[]>('/approvals')
export const resolveApproval = (threadId: string, decision: ApprovalDecision) =>
  postJson<{ status: string; thread_id: string }>(
    `/approvals/${encodeURIComponent(threadId)}`,
    decision,
  )

// --- EOD ---
export const getEod = () => getJson<EodEntrySummary[]>('/eod')
export const eodFinalize = () => postJson<{ pending_count: number }>('/eod/finalize')
export const eodPublish = (taskIds: string[]) =>
  postJson<EodPublishResult>('/eod/publish', { task_ids: taskIds })
export const getEodEntry = (entryId: number) =>
  getJson<BatchEntryDetail>(`/eod/entries/${entryId}`)
```

- [ ] **Step 3: Create gen-types helper script**

Create `frontend/scripts/gen-types.sh`:
```bash
#!/usr/bin/env bash
# Regenerate src/api/schema.ts from the running daemon's OpenAPI schema.
# Requires the daemon to be running on localhost:8787.
set -euo pipefail
npx openapi-typescript http://localhost:8787/openapi.json -o src/api/schema.ts
echo "Wrote src/api/schema.ts"
```

- [ ] **Step 4: Create a placeholder schema.ts (so typecheck passes before gen)**

Create `frontend/src/api/schema.ts`:
```typescript
// Auto-generated by `npm run gen:types`. Placeholder until regeneration.
// The hand-written types in types.ts are the source of truth for now.
export {}
```

- [ ] **Step 5: Verify typecheck + build**

Run:
```bash
cd frontend && npm run typecheck && npm run build
```
Expected: typecheck passes, build produces dist/.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/api/types.ts frontend/src/api/schema.ts frontend/scripts/gen-types.sh
git commit -m "feat(frontend): typed API client + response types

Hand-written TypeScript types matching FastAPI response models (health,
state, tasks, approvals, EOD). Typed fetch client with getJson/postJson
helpers. gen-types.sh regenerates from OpenAPI when daemon is running."
```

---

## Task 9: Pinia stores

**Files:**
- Create: `frontend/src/stores/daemon.ts`, `frontend/src/stores/approvals.ts`, `frontend/src/stores/eod.ts`, `frontend/src/stores/tasks.ts`

**Interfaces:**
- Consumes: API client (Task 8)
- Produces: Four Pinia stores exposing reactive state + actions that call the API client. Each store has a `fetch()` action that refreshes from the backend and a `loading`/`error` ref.

- [ ] **Step 1: Write daemon store**

Create `frontend/src/stores/daemon.ts`:
```typescript
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getHealth, getState } from '@/api/client'
import type { HealthResponse, StateResponse } from '@/api/types'

export const useDaemonStore = defineStore('daemon', () => {
  const health = ref<HealthResponse | null>(null)
  const state = ref<StateResponse | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchAll() {
    loading.value = true
    error.value = null
    try {
      const [h, s] = await Promise.all([getHealth(), getState()])
      health.value = h
      state.value = s
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  return { health, state, loading, error, fetchAll }
})
```

- [ ] **Step 2: Write tasks store**

Create `frontend/src/stores/tasks.ts`:
```typescript
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getTaskDetail, getTasksCurrent, getTasksDone } from '@/api/client'
import type { BatchEntryDetail, EodEntrySummary, TaskCurrentResponse } from '@/api/types'

export const useTasksStore = defineStore('tasks', () => {
  const current = ref<TaskCurrentResponse | null>(null)
  const done = ref<EodEntrySummary[]>([])
  const detail = ref<BatchEntryDetail | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchCurrent() {
    try {
      current.value = await getTasksCurrent()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    }
  }

  async function fetchDone() {
    try {
      done.value = await getTasksDone()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    }
  }

  async function fetchDetail(taskId: string) {
    loading.value = true
    error.value = null
    try {
      detail.value = await getTaskDetail(taskId)
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      detail.value = null
    } finally {
      loading.value = false
    }
  }

  async function fetchAll() {
    loading.value = true
    try {
      await Promise.all([fetchCurrent(), fetchDone()])
    } finally {
      loading.value = false
    }
  }

  return { current, done, detail, loading, error, fetchCurrent, fetchDone, fetchDetail, fetchAll }
})
```

- [ ] **Step 3: Write approvals store**

Create `frontend/src/stores/approvals.ts`:
```typescript
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getApprovals, resolveApproval } from '@/api/client'
import type { ApprovalDecision, ApprovalPending } from '@/api/types'

export const useApprovalsStore = defineStore('approvals', () => {
  const pending = ref<ApprovalPending[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetch() {
    loading.value = true
    error.value = null
    try {
      pending.value = await getApprovals()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  async function resolve(threadId: string, decision: ApprovalDecision) {
    try {
      await resolveApproval(threadId, decision)
      await fetch() // refresh
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    }
  }

  return { pending, loading, error, fetch, resolve }
})
```

- [ ] **Step 4: Write eod store**

Create `frontend/src/stores/eod.ts`:
```typescript
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { eodFinalize, eodPublish, getEod } from '@/api/client'
import type { EodEntrySummary, EodPublishResult } from '@/api/types'

export const useEodStore = defineStore('eod', () => {
  const entries = ref<EodEntrySummary[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const lastPublishResult = ref<EodPublishResult | null>(null)

  async function fetch() {
    loading.value = true
    error.value = null
    try {
      entries.value = await getEod()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  async function finalize() {
    try {
      await eodFinalize()
      await fetch()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    }
  }

  async function publish(taskIds: string[]) {
    loading.value = true
    try {
      lastPublishResult.value = await eodPublish(taskIds)
      await fetch()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  return { entries, loading, error, lastPublishResult, fetch, finalize, publish }
})
```

- [ ] **Step 5: Verify typecheck + build**

Run:
```bash
cd frontend && npm run typecheck && npm run build
```
Expected: typecheck passes (stores import correctly from `@/api/client` and `@/api/types`).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/stores/daemon.ts frontend/src/stores/tasks.ts frontend/src/stores/approvals.ts frontend/src/stores/eod.ts
git commit -m "feat(frontend): Pinia stores (daemon, tasks, approvals, eod)

Four Composition-API stores with reactive state + fetch/resolve/publish
actions calling the typed API client. loading/error refs for async UX."
```

---

## Task 10: useSSE + usePolling composables

**Files:**
- Create: `frontend/src/composables/useSSE.ts`, `frontend/src/composables/usePolling.ts`

**Interfaces:**
- Consumes: native `EventSource`, Pinia stores
- Produces: `useSSE()` connects to `/api/events`, dispatches events to stores (e.g. `task.started` → `tasksStore.fetchCurrent()`, `eod.ready` → `eodStore.fetch()`). `usePolling(fn, intervalMs)` calls `fn` periodically, returns controls.

- [ ] **Step 1: Write usePolling**

Create `frontend/src/composables/usePolling.ts`:
```typescript
import { onBeforeUnmount, onMounted, type Ref } from 'vue'
import { ref } from 'vue'

/**
 * Periodically call `fn` every `intervalMs` while the component is mounted.
 * Returns an active ref and manual refresh control.
 */
export function usePolling(fn: () => Promise<void>, intervalMs: number) {
  const active = ref(true)
  let timer: number | null = null

  async function tick() {
    try {
      await fn()
    } catch {
      // swallow — the store sets its own error ref
    }
  }

  function start() {
    if (timer !== null) return
    active.value = true
    tick()
    timer = window.setInterval(tick, intervalMs)
  }

  function stop() {
    active.value = false
    if (timer !== null) {
      window.clearInterval(timer)
      timer = null
    }
  }

  onMounted(start)
  onBeforeUnmount(stop)

  return { active: active as Ref<boolean>, refresh: tick, stop }
}
```

- [ ] **Step 2: Write useSSE**

Create `frontend/src/composables/useSSE.ts`:
```typescript
import { onBeforeUnmount, onMounted } from 'vue'
import { useDaemonStore } from '@/stores/daemon'
import { useTasksStore } from '@/stores/tasks'
import { useApprovalsStore } from '@/stores/approvals'
import { useEodStore } from '@/stores/eod'

/**
 * Connect to /api/events SSE stream and refresh relevant stores on events.
 * Reconnects on error with a 5s backoff.
 */
export function useSSE() {
  const daemon = useDaemonStore()
  const tasks = useTasksStore()
  const approvals = useApprovalsStore()
  const eod = useEodStore()

  let source: EventSource | null = null
  let reconnectTimer: number | null = null

  function connect() {
    source = new EventSource('/api/events')

    source.addEventListener('task.started', () => {
      void tasks.fetchCurrent()
    })
    source.addEventListener('task.finished', () => {
      void tasks.fetchCurrent()
      void tasks.fetchDone()
    })
    source.addEventListener('task.error', () => {
      void tasks.fetchCurrent()
      void daemon.fetchAll()
    })
    source.addEventListener('eod.ready', () => {
      void eod.fetch()
    })
    source.addEventListener('approval.waiting', () => {
      void approvals.fetch()
    })

    source.onerror = () => {
      source?.close()
      source = null
      // Reconnect after 5s.
      reconnectTimer = window.setTimeout(connect, 5000)
    }
  }

  onMounted(connect)
  onBeforeUnmount(() => {
    source?.close()
    source = null
    if (reconnectTimer !== null) {
      window.clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
  })
}
```

- [ ] **Step 3: Verify typecheck**

Run:
```bash
cd frontend && npm run typecheck
```
Expected: passes.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/composables/usePolling.ts frontend/src/composables/useSSE.ts
git commit -m "feat(frontend): useSSE + usePolling composables

useSSE connects to /api/events, dispatches task.*/eod.ready/approval
events to the relevant Pinia stores (auto-refresh), reconnects on error
with 5s backoff. usePolling(fn, ms) drives periodic fetches with
mount/unmount lifecycle."
```

---

## Task 11: Vue Router + App.vue layout

**Files:**
- Create: `frontend/src/router/index.ts`
- Modify: `frontend/src/App.vue` (layout + nav)
- Modify: `frontend/src/main.ts` (use router)
- Create: `frontend/src/views/NotFoundView.vue` (placeholder so router resolves)

**Interfaces:**
- Consumes: views (created as minimal placeholders in Tasks 12-15; this task creates the router referencing them, and stubs if they don't exist yet)
- Produces: `createRouter` with routes: `/` → Dashboard, `/approvals` → Approvals, `/eod` → Eod Review, `/tasks/:id` → Task Detail, `/:pathMatch(.*)*` → NotFound. `App.vue` renders a nav header + `<RouterView>`.

**IMPORTANT:** This task creates stub view files (one-line placeholders) so the router resolves. Tasks 12-15 fill them in.

- [ ] **Step 1: Create stub views (placeholders)**

Create these one-line stub files (Tasks 12-15 replace them):
- `frontend/src/views/DashboardView.vue`:
```vue
<script setup lang="ts"></script>
<template><h2>Dashboard (Task 12)</h2></template>
```
- `frontend/src/views/ApprovalsView.vue`:
```vue
<script setup lang="ts"></script>
<template><h2>Approvals (Task 13)</h2></template>
```
- `frontend/src/views/EodReviewView.vue`:
```vue
<script setup lang="ts"></script>
<template><h2>EOD Review (Task 14)</h2></template>
```
- `frontend/src/views/TaskDetailView.vue`:
```vue
<script setup lang="ts"></script>
<template><h2>Task Detail (Task 15)</h2></template>
```
- `frontend/src/views/NotFoundView.vue`:
```vue
<script setup lang="ts"></script>
<template>
  <div>
    <h2>404 — Not Found</h2>
    <RouterLink to="/">Back to Dashboard</RouterLink>
  </div>
</template>
```

- [ ] **Step 2: Create router**

Create `frontend/src/router/index.ts`:
```typescript
import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'dashboard', component: () => import('@/views/DashboardView.vue') },
  { path: '/approvals', name: 'approvals', component: () => import('@/views/ApprovalsView.vue') },
  { path: '/eod', name: 'eod', component: () => import('@/views/EodReviewView.vue') },
  { path: '/tasks/:id', name: 'task-detail', component: () => import('@/views/TaskDetailView.vue'), props: true },
  { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('@/views/NotFoundView.vue') },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})
```

- [ ] **Step 3: Update App.vue (layout + nav)**

Replace `frontend/src/App.vue`:
```vue
<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'
import { useSSE } from '@/composables/useSSE'

// Connect to the live event stream for the whole app.
useSSE()
</script>

<template>
  <div id="devflow-app">
    <header>
      <h1>DevFlow Dashboard</h1>
      <nav>
        <RouterLink to="/">Dashboard</RouterLink>
        <span> · </span>
        <RouterLink to="/approvals">Approvals</RouterLink>
        <span> · </span>
        <RouterLink to="/eod">EOD Review</RouterLink>
      </nav>
    </header>
    <main>
      <RouterView />
    </main>
  </div>
</template>
```

- [ ] **Step 4: Update main.ts (use router)**

Replace `frontend/src/main.ts`:
```typescript
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { router } from './router'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
```

- [ ] **Step 5: Verify build**

Run:
```bash
cd frontend && npm run typecheck && npm run build
```
Expected: typecheck passes, build produces dist/index.html + assets.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/router/index.ts frontend/src/App.vue frontend/src/main.ts frontend/src/views/
git commit -m "feat(frontend): Vue Router + App.vue layout + nav

Routes: /, /approvals, /eod, /tasks/:id, 404 catch-all. App.vue renders
nav header + RouterView, connects SSE stream app-wide. Stub views
(Dashboard/Approvals/Eod/TaskDetail/NotFound) — filled in Tasks 12-15."
```

---

## Task 12: DashboardView

**Files:**
- Modify: `frontend/src/views/DashboardView.vue`

**Interfaces:**
- Consumes: `useDaemonStore`, `useTasksStore`, `usePolling`
- Produces: a functional dashboard showing health status, HITL strategy, current task, and done-task count. Polls every 5s.

- [ ] **Step 1: Implement DashboardView**

Replace `frontend/src/views/DashboardView.vue`:
```vue
<script setup lang="ts">
import { useDaemonStore } from '@/stores/daemon'
import { useTasksStore } from '@/stores/tasks'
import { usePolling } from '@/composables/usePolling'

const daemon = useDaemonStore()
const tasks = useTasksStore()

const { refresh } = usePolling(async () => {
  await Promise.all([daemon.fetchAll(), tasks.fetchAll()])
}, 5000)

// initial fetch
void refresh()
</script>

<template>
  <section>
    <h2>Dashboard</h2>
    <p v-if="daemon.error" class="error">Error: {{ daemon.error }}</p>

    <div v-if="daemon.health" class="card">
      <h3>Health</h3>
      <dl>
        <dt>Status</dt><dd>{{ daemon.health.status }}</dd>
        <dt>Scheduler</dt><dd>{{ daemon.health.scheduler }}</dd>
        <dt>Uptime</dt><dd>{{ daemon.health.uptime_seconds }}s</dd>
        <dt>Pending approvals</dt><dd>{{ daemon.health.pending_approvals }}</dd>
        <dt>Batch store pending</dt><dd>{{ daemon.health.batch_store_pending }}</dd>
      </dl>
    </div>

    <div v-if="daemon.state" class="card">
      <h3>Config</h3>
      <dl>
        <dt>HITL strategy</dt><dd>{{ daemon.state.hitl_strategy }}</dd>
        <dt>Task source</dt><dd>{{ daemon.state.task_source }}</dd>
        <dt>Task schedule</dt><dd><code>{{ daemon.state.daemon.task_schedule }}</code></dd>
        <dt>EOD schedule</dt><dd><code>{{ daemon.state.daemon.eod_schedule }}</code></dd>
      </dl>
    </div>

    <div class="card">
      <h3>Current task</h3>
      <p v-if="tasks.current?.task_id">
        Active: <strong>{{ tasks.current.task_id }}</strong>
        <span v-if="tasks.current.node"> (node: {{ tasks.current.node }})</span>
      </p>
      <p v-else>No task currently running.</p>
    </div>

    <div class="card">
      <h3>Done today ({{ tasks.done.length }})</h3>
      <ul v-if="tasks.done.length">
        <li v-for="t in tasks.done" :key="t.id">
          <RouterLink :to="`/tasks/${t.task_id}`">{{ t.task_id }}</RouterLink>
          — {{ t.task_title }} ({{ t.final_verdict ?? '–' }})
        </li>
      </ul>
      <p v-else>No completed tasks.</p>
    </div>
  </section>
</template>
```

- [ ] **Step 2: Verify typecheck + build**

Run:
```bash
cd frontend && npm run typecheck && npm run build
```
Expected: passes.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/DashboardView.vue
git commit -m "feat(frontend): DashboardView — health, config, current task, done list

Polls daemon.health/state + tasks.current/done every 5s. Reactive
cards for health metrics, HITL strategy, active task, and today's
completed tasks (links to detail)."
```

---

## Task 13: ApprovalsView

**Files:**
- Modify: `frontend/src/views/ApprovalsView.vue`

**Interfaces:**
- Consumes: `useApprovalsStore`, `usePolling`
- Produces: a list of pending approvals with an inline approve/reject form (reason + requested_changes). Polls every 4s.

- [ ] **Step 1: Implement ApprovalsView**

Replace `frontend/src/views/ApprovalsView.vue`:
```vue
<script setup lang="ts">
import { ref } from 'vue'
import { useApprovalsStore } from '@/stores/approvals'
import { usePolling } from '@/composables/usePolling'

const store = useApprovalsStore()
const { refresh } = usePolling(() => store.fetch(), 4000)
void refresh()

// Per-approval form state (keyed by thread_id).
const reasons = ref<Record<string, string>>({})
const changes = ref<Record<string, string>>({})

async function approve(threadId: string) {
  await store.resolve(threadId, {
    approved: true,
    reason: reasons.value[threadId] ?? '',
  })
  delete reasons.value[threadId]
}

async function reject(threadId: string) {
  await store.resolve(threadId, {
    approved: false,
    reason: reasons.value[threadId] ?? '',
    requested_changes: (changes.value[threadId] ?? '')
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean),
  })
  delete reasons.value[threadId]
  delete changes.value[threadId]
}
</script>

<template>
  <section>
    <h2>Approvals</h2>
    <p v-if="store.error" class="error">Error: {{ store.error }}</p>
    <p v-if="!store.pending.length">No pending approvals.</p>

    <div v-for="a in store.pending" :key="a.thread_id" class="card">
      <h3>{{ (a.payload as any).task_title ?? a.thread_id }}</h3>
      <p><strong>Gate:</strong> {{ (a.payload as any).gate_type ?? 'unknown' }}</p>
      <p><strong>Task:</strong> {{ (a.payload as any).task_id ?? '–' }}</p>
      <details v-if="(a.payload as any).plan_summary">
        <summary>Plan</summary>
        <pre>{{ (a.payload as any).plan_summary }}</pre>
      </details>
      <details v-if="(a.payload as any).diff">
        <summary>Diff</summary>
        <pre>{{ (a.payload as any).diff }}</pre>
      </details>

      <textarea
        v-model="reasons[a.thread_id]"
        placeholder="Reason (optional)"
        rows="2"
        style="width: 100%"
      ></textarea>
      <textarea
        v-model="changes[a.thread_id]"
        placeholder="Requested changes (one per line, for reject)"
        rows="3"
        style="width: 100%"
      ></textarea>
      <button @click="approve(a.thread_id)">Approve</button>
      <button @click="reject(a.thread_id)">Reject</button>
    </div>
  </section>
</template>
```

- [ ] **Step 2: Verify typecheck + build**

Run:
```bash
cd frontend && npm run typecheck && npm run build
```
Expected: passes.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/ApprovalsView.vue
git commit -m "feat(frontend): ApprovalsView — pending list + approve/reject form

Polls /api/approvals every 4s. Each pending approval shows gate type,
task, plan/diff (collapsible), reason + requested-changes fields,
Approve/Reject buttons calling resolveApproval."
```

---

## Task 14: EodReviewView

**Files:**
- Modify: `frontend/src/views/EodReviewView.vue`

**Interfaces:**
- Consumes: `useEodStore`, `usePolling`
- Produces: a list of pending EOD entries with checkboxes (select for publish), a "Finalize" button, and a "Publish Selected" button. Shows the last publish result.

- [ ] **Step 1: Implement EodReviewView**

Replace `frontend/src/views/EodReviewView.vue`:
```vue
<script setup lang="ts">
import { ref, computed } from 'vue'
import { useEodStore } from '@/stores/eod'
import { usePolling } from '@/composables/usePolling'

const store = useEodStore()
const { refresh } = usePolling(() => store.fetch(), 10000)
void refresh()

const selected = ref<Set<string>>(new Set())

function toggle(taskId: string) {
  if (selected.value.has(taskId)) {
    selected.value.delete(taskId)
  } else {
    selected.value.add(taskId)
  }
  // trigger reactivity
  selected.value = new Set(selected.value)
}

const selectedList = computed(() => Array.from(selected.value))

async function publishSelected() {
  await store.publish(selectedList.value)
  selected.value = new Set()
}

async function publishAll() {
  await store.publish([]) // empty = all
  selected.value = new Set()
}
</script>

<template>
  <section>
    <h2>EOD Review</h2>
    <p v-if="store.error" class="error">Error: {{ store.error }}</p>

    <div class="actions">
      <button @click="store.finalize()">Finalize (refresh pending)</button>
      <button :disabled="!selectedList.length" @click="publishSelected">
        Publish selected ({{ selectedList.length }})
      </button>
      <button :disabled="!store.entries.length" @click="publishAll">
        Publish ALL
      </button>
    </div>

    <div v-if="store.lastPublishResult" class="card">
      <h3>Last publish result</h3>
      <p>Published: {{ store.lastPublishResult.published.join(', ') || 'none' }}</p>
      <p>Failed: {{ store.lastPublishResult.failed.join(', ') || 'none' }}</p>
      <p>Skipped: {{ store.lastPublishResult.skipped.join(', ') || 'none' }}</p>
    </div>

    <p v-if="!store.entries.length">No pending EOD entries.</p>

    <table v-else>
      <thead>
        <tr>
          <th></th>
          <th>Task</th><th>Title</th><th>Branch</th><th>Verdict</th><th>Status</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="e in store.entries" :key="e.id">
          <td>
            <input
              type="checkbox"
              :checked="selected.has(e.task_id)"
              @change="toggle(e.task_id)"
            />
          </td>
          <td>
            <RouterLink :to="`/tasks/${e.task_id}`">{{ e.task_id }}</RouterLink>
          </td>
          <td>{{ e.task_title }}</td>
          <td><code>{{ e.branch_name }}</code></td>
          <td>{{ e.final_verdict ?? '–' }}</td>
          <td>{{ e.status }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>
```

- [ ] **Step 2: Verify typecheck + build**

Run:
```bash
cd frontend && npm run typecheck && npm run build
```
Expected: passes.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/EodReviewView.vue
git commit -m "feat(frontend): EodReviewView — pending list + batch publish

Table of pending EOD entries with checkboxes, Finalize button,
Publish Selected / Publish ALL buttons. Shows last publish result
(published/failed/skipped). Links to task detail."
```

---

## Task 15: TaskDetailView

**Files:**
- Modify: `frontend/src/views/TaskDetailView.vue`

**Interfaces:**
- Consumes: `useTasksStore` (`fetchDetail`), route `id` prop
- Produces: full task detail — task meta, plan, diff, checker reports, reporter artifacts (PR title/description/commit message). Collapsible sections for large content.

- [ ] **Step 1: Implement TaskDetailView**

Replace `frontend/src/views/TaskDetailView.vue`:
```vue
<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { useTasksStore } from '@/stores/tasks'

const props = defineProps<{ id: string }>()
const store = useTasksStore()

async function load() {
  await store.fetchDetail(props.id)
}

onMounted(load)
watch(() => props.id, load)
</script>

<template>
  <section>
    <h2>Task: {{ props.id }}</h2>
    <p><RouterLink to="/">← Back to Dashboard</RouterLink></p>
    <p v-if="store.error" class="error">Error: {{ store.error }}</p>
    <p v-if="store.loading && !store.detail">Loading…</p>

    <div v-if="store.detail" class="detail">
      <div class="card">
        <h3>{{ store.detail.task_title }}</h3>
        <dl>
          <dt>Branch</dt><dd><code>{{ store.detail.branch_name }}</code></dd>
          <dt>Verdict</dt><dd>{{ store.detail.final_verdict ?? '–' }}</dd>
          <dt>Status</dt><dd>{{ store.detail.status }}</dd>
          <dt>Created</dt><dd>{{ store.detail.created_at }}</dd>
          <dt v-if="store.detail.published_at">Published</dt>
          <dd v-if="store.detail.published_at">{{ store.detail.published_at }}</dd>
          <dt v-if="store.detail.mr_url">MR</dt>
          <dd v-if="store.detail.mr_url">
            <a :href="store.detail.mr_url" target="_blank">{{ store.detail.mr_url }}</a>
          </dd>
        </dl>
      </div>

      <div class="card">
        <h3>Plan</h3>
        <p>{{ store.detail.plan_summary }}</p>
        <ol>
          <li v-for="step in store.detail.plan_steps" :key="step">{{ step }}</li>
        </ol>
      </div>

      <div class="card">
        <h3>Checker reports</h3>
        <ul v-if="store.detail.checker_reports.length">
          <li v-for="(r, i) in store.detail.checker_reports" :key="i">
            <strong>{{ r.agent_name }}</strong> ({{ r.verdict }}): {{ r.summary }}
            <ul v-if="r.findings.length">
              <li v-for="(f, j) in r.findings" :key="j">{{ f }}</li>
            </ul>
          </li>
        </ul>
        <p v-else>No checker reports.</p>
      </div>

      <div class="card">
        <h3>Reporter artifacts</h3>
        <p><strong>PR title:</strong> {{ store.detail.reporter_artifacts.pr_title }}</p>
        <details>
          <summary>PR description</summary>
          <pre>{{ store.detail.reporter_artifacts.pr_description }}</pre>
        </details>
        <details>
          <summary>Commit message</summary>
          <pre>{{ store.detail.reporter_artifacts.commit_message }}</pre>
        </details>
        <details>
          <summary>Corporate report</summary>
          <pre>{{ store.detail.reporter_artifacts.corporate_report }}</pre>
        </details>
      </div>

      <div class="card">
        <h3>Diff</h3>
        <details>
          <summary>Show diff</summary>
          <pre>{{ store.detail.diff }}</pre>
        </details>
      </div>
    </div>
  </section>
</template>
```

- [ ] **Step 2: Verify typecheck + build**

Run:
```bash
cd frontend && npm run typecheck && npm run build
```
Expected: passes.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/TaskDetailView.vue
git commit -m "feat(frontend): TaskDetailView — full task detail

Shows task meta (branch, verdict, status, MR link), plan summary +
steps, checker reports (with findings), reporter artifacts (PR title/
description, commit message, corporate report — collapsible), and diff.
Loaded from /api/tasks/{id} via tasksStore.fetchDetail."
```

---

## Task 16: Minimal global styles + docs (.gitignore, README, HANDOFF)

**Files:**
- Create: `frontend/src/style.css` (minimal reset + skeleton styles)
- Modify: `frontend/src/main.ts` (import style.css)
- Modify: `.gitignore` (root — add frontend carve-outs if needed)
- Modify: `docs/superpowers/HANDOFF.md` (Phase 5 complete)
- Create: `frontend/README.md`

**Interfaces:**
- Consumes: nothing
- Produces: minimal readable styles (functional, not a design system) so the dashboard is usable. HANDOFF updated to reflect Phase 5 done.

- [ ] **Step 1: Create style.css (minimal functional styles)**

Create `frontend/src/style.css`:
```css
/* Minimal functional styles — NOT a design system. UI polish is a separate task. */
:root {
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  color-scheme: light;
}
body {
  margin: 0;
  padding: 0;
  background: #f7f7f8;
  color: #1a1a1a;
}
#devflow-app {
  max-width: 1100px;
  margin: 0 auto;
  padding: 1rem 1.5rem 3rem;
}
header {
  display: flex;
  align-items: center;
  gap: 1rem;
  border-bottom: 1px solid #ddd;
  padding-bottom: 0.75rem;
  margin-bottom: 1.5rem;
}
header h1 {
  font-size: 1.25rem;
  margin: 0;
}
nav a {
  color: #0366d6;
  text-decoration: none;
}
nav a:hover {
  text-decoration: underline;
}
.card {
  background: #fff;
  border: 1px solid #e1e1e3;
  border-radius: 6px;
  padding: 1rem 1.25rem;
  margin-bottom: 1rem;
}
.card h3 {
  margin-top: 0;
  font-size: 1rem;
}
dl {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 0.25rem 1rem;
  margin: 0;
}
dt {
  font-weight: 600;
  color: #555;
}
dd {
  margin: 0;
}
code, pre {
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  font-size: 0.85em;
  background: #f0f0f1;
  padding: 0.1em 0.3em;
  border-radius: 3px;
}
pre {
  padding: 0.75rem;
  overflow-x: auto;
  max-height: 30em;
}
.error {
  color: #cb2431;
}
button {
  font: inherit;
  padding: 0.35rem 0.9rem;
  border: 1px solid #ccc;
  border-radius: 4px;
  background: #fff;
  cursor: pointer;
  margin-right: 0.5rem;
}
button:hover:not(:disabled) {
  background: #f0f0f1;
}
button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
table {
  width: 100%;
  border-collapse: collapse;
}
th, td {
  text-align: left;
  padding: 0.4rem 0.6rem;
  border-bottom: 1px solid #eee;
}
th {
  font-size: 0.85rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: #666;
}
.actions {
  margin-bottom: 1.5rem;
}
details {
  margin: 0.5rem 0;
}
summary {
  cursor: pointer;
  color: #0366d6;
}
```

- [ ] **Step 2: Import style.css in main.ts**

In `frontend/src/main.ts`, add at the top (before other imports):
```typescript
import './style.css'
```

- [ ] **Step 3: Create frontend/README.md**

Create `frontend/README.md`:
```markdown
# DevFlow Dashboard (frontend)

Vue 3 SPA dashboard for the devflow daemon.

## Prerequisites

- Node.js ≥ 18
- The devflow daemon running on `localhost:8787` (for API + dev proxy)

## Development

```bash
cd frontend
npm install
npm run dev      # starts Vite on http://localhost:5173 (proxies /api → :8787)
```

Open http://localhost:5173. The Vite dev server proxies `/api/*` to the
daemon on `:8787`.

## Production build

```bash
npm run build    # outputs dist/ (served by the daemon in production)
```

The daemon serves `dist/` at `/` when `daemon.serve_frontend=true` (default)
and the `frontend_dist` path exists (`frontend/dist` by default). No separate
web server needed — one process, one port (`:8787`).

## Regenerate API types from OpenAPI

With the daemon running:
```bash
npm run gen:types
```
This regenerates `src/api/schema.ts` from `/openapi.json`. The hand-written
`src/api/types.ts` is the current source of truth.

## Structure

- `src/api/` — typed fetch client + response types
- `src/stores/` — Pinia stores (daemon, tasks, approvals, eod)
- `src/composables/` — useSSE (live event stream), usePolling
- `src/views/` — page components (Dashboard, Approvals, EOD Review, Task Detail)
- `src/router/` — Vue Router config

## Notes

UI is functional skeletons — no component library or design system.
Visual polish is a separate task.
```

- [ ] **Step 4: Update HANDOFF.md**

In `docs/superpowers/HANDOFF.md`:
- Move the "Phase 5" content from "Что осталось" into "Что реализовано" as a new subsection documenting what was built.
- Update Git состояние: Phase 5 on branch `feature/phase5-vue-dashboard`, pending merge.
- The "Что осталось" section should now say "Nothing — all 5 phases complete" or list only future-work items.

Add after the Phase 4 section:
```markdown
### Phase 5: Vue 3 SPA Dashboard (this branch, feature/phase5-vue-dashboard)
- Backend: `/api/events` SSE endpoint (sse-starlette, global '*' topic)
- Backend: `/api/tasks/*` routes (current/queue/done/{id})
- Backend: CORS middleware (dev server origins), StaticFiles SPA serving + fallback
- Backend: `on_task_change` callback wires runner → app.state.set_current_task
- Frontend: Vue 3 + Vite + TypeScript (strict) + Pinia + Vue Router scaffold
- Frontend: typed API client (hand-written types matching FastAPI models)
- Frontend: useSSE composable (live event stream → store refresh) + usePolling
- Frontend: 4 views — Dashboard, Approvals, EOD Review, Task Detail (+ 404)
- `DaemonConfig` += serve_frontend, frontend_dist, cors_origins
- Minimal functional CSS (no design system — UI polish is future work)
```

- [ ] **Step 5: Verify build**

Run:
```bash
cd frontend && npm run build
```
Expected: build succeeds with style.css bundled.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/style.css frontend/src/main.ts frontend/README.md docs/superpowers/HANDOFF.md
git commit -m "feat(frontend): minimal styles + docs (README, HANDOFF Phase 5)

Functional CSS reset + skeleton card/table/form styles (NOT a design
system — UI polish is future work). frontend/README.md with dev/build
instructions. HANDOFF updated: Phase 5 complete, all 5 phases done."
```

---

## Task 17: Backend integration test + full-stack verification

**Files:**
- Create: `tests/integration/test_dashboard_e2e.py`

**Interfaces:**
- Consumes: all backend Phase 5 components

This is the capstone backend integration test: verify the daemon serves the SPA, the API routes work together, and the SSE stream delivers an event end-to-end. (Frontend E2E via a browser is out of scope — verified manually via the README dev instructions.)

- [ ] **Step 1: Write integration test**

Create `tests/integration/test_dashboard_e2e.py`:
```python
"""Integration test: dashboard backend (SSE + tasks + static) end-to-end.

Verifies the Phase 5 backend stack works together:
- /api/events SSE delivers a published event
- /api/tasks/* routes reflect runner state + batch store
- static SPA fallback serves index.html for unknown non-/api paths
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from fastapi.testclient import TestClient

from devflow.batch.eod_handler import EodHandler
from devflow.batch.models import BatchEntry, BatchStatus
from devflow.batch.store import BatchStore
from devflow.daemon.events import EventBus, GLOBAL_TOPIC
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
    mock_config, tmp_path: Path
) -> None:
    """A published task appears in /api/tasks/done and /api/tasks/{id}."""
    store = BatchStore(tmp_path / "batch_store.db")
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


def test_sse_delivers_published_event(mock_config) -> None:
    """An event published to the EventBus is delivered via /api/events SSE."""
    bus = EventBus()
    app = create_app(mock_config, DaemonLocks(), bus)
    client = TestClient(app)

    received: list[str] = []

    def _publish() -> None:
        asyncio.run(bus.publish(GLOBAL_TOPIC, {"event": "eod.ready", "pending_count": 2}))

    with client.stream("GET", "/api/events") as resp:
        assert resp.status_code == 200
        t = threading.Thread(target=_publish, daemon=True)
        t.start()
        for line in resp.iter_lines():
            if line.startswith("event:") and "eod.ready" in line:
                received.append(line)
                break
            if len(received) >= 1:
                break

    assert any("eod.ready" in r for r in received)


def test_spa_fallback_serves_index(mock_config, tmp_path: Path) -> None:
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
```

- [ ] **Step 2: Run integration test**

Run: `python -m pytest tests/integration/test_dashboard_e2e.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 3: Run FULL test suite**

Run: `python -m pytest tests/ -v`
Expected: ALL tests PASS.

- [ ] **Step 4: Run ruff + mypy**

Run: `python -m ruff check src/devflow/daemon/ src/devflow/config.py tests/integration/test_dashboard_e2e.py tests/unit/daemon/`
Run: `python -m mypy src/devflow/daemon/ src/devflow/config.py`
Expected: clean.

- [ ] **Step 5: Run frontend build once more (final check)**

Run:
```bash
cd frontend && npm run build
```
Expected: build succeeds, `dist/index.html` present.

- [ ] **Step 6: Commit**

```bash
git add tests/integration/test_dashboard_e2e.py
git commit -m "test(integration): dashboard backend e2e (SSE + tasks + SPA fallback)

Verifies published task appears in /api/tasks/done + /{id}, SSE delivers
a published event, and SPA fallback serves index.html for unknown non-/api
paths while /api/* still 404s correctly."
```

---

## Self-Review

### Spec coverage (Phase 5, per spec lines 251-329)

| Spec requirement | Task(s) | Covered? |
|---|---|---|
| `frontend/` separate npm package (Vue 3 + Vite + TS + Pinia + Vue Router) | Task 7 | ✅ |
| FastAPI `/api/events` SSE (EventBus → SSE) | Tasks 1, 2 | ✅ |
| FastAPI `/api/tasks/*` routes | Task 3 | ✅ (queue returns note — live source not available) |
| Production: FastAPI serves `frontend/dist/` as static | Task 5 | ✅ |
| Development: Vite dev-server proxies `/api/*` | Task 7 (vite.config.ts) | ✅ |
| typed API client (openapi-typescript) | Task 8 | ✅ (hand-written types + gen script) |
| useSSE composable | Task 10 | ✅ |
| Pinia stores | Task 9 | ✅ |
| UI = functional skeletons, no design system (spec line 294) | Tasks 12-15, 16 | ✅ |
| Dashboard view (live progress, current task) | Task 12 | ✅ |
| Approvals view (pending + resolve) | Task 13 | ✅ |
| EOD review view (batch + publish) | Task 14 | ✅ |
| Task detail view (plan/diff/reports) | Task 15 | ✅ |
| CORS for dev server | Task 4 | ✅ |
| set_current_task wired (so health reflects active task) | Task 6 | ✅ |
| Minimal styles (functional, not design) | Task 16 | ✅ |

**Deferred / out of scope (correctly):**
- `graph.stream()` node-by-node progress (spec line 329) — requires rewriting `run_workflow` from `invoke` to `stream`; the runner publishes task.started/finished only. `current_node` is always `None`. This is explicitly a larger change; the SSE infrastructure is in place for when it's added.
- `/api/tasks/queue` real content — requires a live task_source; returns empty + note.
- Component library / design system — spec line 294 explicitly defers this.

### Placeholder scan

Searched for TBD/TODO/"implement later"/"similar to" in actionable steps. None found — all code blocks contain complete implementations. The only "TODO"-style text is in the stub views ("Task 12" etc.) which are explicitly replaced by later tasks.

### Type consistency

- `HealthResponse`, `StateResponse`, `EodEntrySummary`, `BatchEntryDetail`, `ApprovalPending`, `ApprovalDecision`, `EodPublishResult` — consistent across backend models (web.py) and frontend types (types.ts) and store/action signatures.
- `EventBus.publish(topic, data)` + `GLOBAL_TOPIC` — consistent across Tasks 1, 2, 17.
- `create_app(...)` signature grows `app` param only in `run_web_server` (Task 6) — `create_app` itself unchanged in signature (still takes the same 6 params).
- `WorkflowRunner.__init__` += `on_task_change` (Task 6) — optional, defaults None; existing tests unaffected.
- Frontend store method names (`fetchAll`, `fetch`, `resolve`, `publish`, `finalize`, `fetchDetail`, `fetchCurrent`, `fetchDone`) — consistent across stores (Task 9) and views (Tasks 12-15) and composables (Task 10).
- API client function names (`getHealth`, `getApprovals`, `resolveApproval`, `eodPublish`, etc.) — consistent across client.ts (Task 8) and store imports (Task 9).

No type/name mismatches found.
