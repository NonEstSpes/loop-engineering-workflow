# Dashboard P2 — Event Log + Approval SSE Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent event history (SQLite EventStore), approval SSE notifications (instant instead of 4s polling), an Activity Log view, and a global toast system.

**Architecture:** EventStore (SQLite `.devflow/events.db`) subscribes to EventBus via a background asyncio task. ApprovalStore gets an optional `event_bus` param and publishes `approval.waiting`/`approval.resolved` on register/resolve. New `GET /api/events/history` endpoint. New `ActivityView.vue` with event feed + filter + live SSE. Global `useToast` composable + toast-container in `App.vue`.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLite, pytest (backend); Vue 3 Composition API, TypeScript, Pinia, Vite (frontend).

## Global Constraints

- **EventStore** mirrors BatchStore/QueueStore pattern: `sqlite3.connect(check_same_thread=False)`, `threading.Lock`, JSON-serialized data.
- **DB path**: `.devflow/events.db`.
- **Max events**: 1000 (auto-prune oldest by id after insert).
- **ApprovalStore publication**: `_publish_sync` helper (asyncio.run with thread fallback, same pattern as `runner._publish`).
- **Event types in history**: task.*, approval.*, queue.* (NOT config — per user decision).
- **Toast**: auto-dismiss 5s, fixed top-right, 4 types (info/success/warning/error).
- **Backend tests**: pytest in `tests/unit/`.
- **Frontend**: no test runner; verify via `npm run typecheck` + `npm run build`.
- **Commit after each task** — conventional commits.
- **Branch**: `feature/phase5-vue-dashboard`.

---

## File Structure

**Backend — new:**
- `src/devflow/batch/event_store.py` — EventStore (SQLite)
- `tests/unit/batch/test_event_store.py`

**Backend — modified:**
- `src/devflow/daemon/approval_store.py` — event_bus param + publish on register/resolve
- `src/devflow/daemon/__main__.py` — EventStore construction + EventBus subscriber + wiring
- `src/devflow/daemon/web.py` — `GET /api/events/history` + event_store param
- `tests/unit/daemon/test_approval_store.py` — SSE publish tests

**Frontend — new:**
- `frontend/src/views/ActivityView.vue`
- `frontend/src/stores/activity.ts`
- `frontend/src/composables/useToast.ts`

**Frontend — modified:**
- `frontend/src/App.vue` — nav «Activity» + toast-container
- `frontend/src/router/index.ts` — `/activity` route
- `frontend/src/composables/useSSE.ts` — approval.waiting/resolved listeners
- `frontend/src/api/client.ts` — getEventHistory
- `frontend/src/api/types.ts` — EventLogEntry
- `frontend/src/style.css` — activity log + toast styles

---

### Task 1: EventStore (SQLite persistent history)

**Files:**
- Create: `src/devflow/batch/event_store.py`
- Test: `tests/unit/batch/test_event_store.py`

**Interfaces:**
- Consumes: Pydantic `BaseModel`, `sqlite3`, `threading.Lock`, `json`
- Produces: `EventLogEntry` model, `EventStore` class with `add(event_type, data)`, `get_recent(limit, event_type)`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/batch/test_event_store.py`:

```python
"""Tests for EventStore — SQLite-backed event history."""

from __future__ import annotations

from pathlib import Path

from devflow.batch.event_store import EventLogEntry, EventStore


def _make_store(tmp_path: Path) -> EventStore:
    return EventStore(str(tmp_path / "events.db"))


def test_add_and_get_recent_round_trip(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.add("task.started", {"task_id": "1", "event": "task.started"})
    store.add("task.finished", {"task_id": "1", "event": "task.finished"})
    result = store.get_recent(limit=10)
    assert len(result) == 2
    # Newest first (highest id first).
    assert result[0].event_type == "task.finished"
    assert result[1].event_type == "task.started"


def test_get_recent_with_limit(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    for i in range(5):
        store.add("task.started", {"i": i})
    result = store.get_recent(limit=3)
    assert len(result) == 3
    # Newest first: i=4, i=3, i=2.
    assert result[0].data["i"] == 4
    assert result[2].data["i"] == 2


def test_get_recent_filter_by_event_type(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.add("task.started", {"event": "task.started"})
    store.add("approval.waiting", {"event": "approval.waiting"})
    store.add("queue.updated", {"event": "queue.updated"})
    # Filter "approval" → prefix match.
    result = store.get_recent(limit=10, event_type="approval")
    assert len(result) == 1
    assert result[0].event_type == "approval.waiting"


def test_get_recent_filter_prefix_match(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.add("task.started", {})
    store.add("task.finished", {})
    store.add("approval.waiting", {})
    # "task" → matches task.started + task.finished.
    result = store.get_recent(limit=10, event_type="task")
    assert len(result) == 2
    assert all(r.event_type.startswith("task") for r in result)


def test_auto_prune_when_over_max(tmp_path: Path) -> None:
    """EventStore prunes to MAX_EVENTS (1000) oldest entries."""
    store = _make_store(tmp_path)
    # The default max is 1000. Override via _MAX_EVENTS for a faster test.
    store._MAX_EVENTS = 5
    for i in range(10):
        store.add("task.started", {"i": i})
    result = store.get_recent(limit=100)
    assert len(result) == 5
    # Oldest 5 pruned; remaining are i=5..9.
    ids = sorted(r.data["i"] for r in result)
    assert ids == [5, 6, 7, 8, 9]


def test_empty_store_returns_empty_list(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    assert store.get_recent(limit=10) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/batch/test_event_store.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write EventStore implementation**

Create `src/devflow/batch/event_store.py`:

```python
"""SQLite-backed event history store.

Subscribes to the EventBus (via a background task in the daemon) and persists
every event to SQLite. The dashboard reads the history via
``GET /api/events/history`` for the Activity Log view.

The DB file lives at ``{repo_path}/.devflow/events.db``. Thread-safe via a
``threading.Lock`` (cross-thread access from daemon task + FastAPI handlers).
Auto-prunes to ``MAX_EVENTS`` (1000) to bound growth.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class EventLogEntry(BaseModel):
    """A single persisted event in the history log."""

    id: int
    timestamp: str  # ISO 8601 UTC
    event_type: str
    data: dict[str, str]


class EventStore:
    """CRUD for event history in SQLite with auto-pruning."""

    _MAX_EVENTS = 1000

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._create_schema()

    def _create_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                data TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def add(self, event_type: str, data: dict[str, object]) -> None:
        """Insert an event. Auto-prunes oldest entries over _MAX_EVENTS."""
        now = datetime.now(timezone.utc).isoformat()
        serialized = json.dumps(data, default=str, ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                "INSERT INTO event_log (timestamp, event_type, data) VALUES (?, ?, ?)",
                (now, event_type, serialized),
            )
            self._conn.commit()
            self._prune()

    def _prune(self) -> None:
        """Delete oldest entries beyond _MAX_EVENTS. Caller holds lock."""
        count_row = self._conn.execute("SELECT COUNT(*) as c FROM event_log").fetchone()
        count = count_row["c"] if count_row else 0
        if count > self._MAX_EVENTS:
            excess = count - self._MAX_EVENTS
            self._conn.execute(
                "DELETE FROM event_log WHERE id IN "
                "(SELECT id FROM event_log ORDER BY id ASC LIMIT ?)",
                (excess,),
            )
            self._conn.commit()
            logger.debug("Pruned %d old events from event_log", excess)

    def get_recent(
        self,
        limit: int = 100,
        event_type: str | None = None,
    ) -> list[EventLogEntry]:
        """Return recent events (newest first), optionally filtered by type prefix."""
        with self._lock:
            if event_type is not None:
                rows = self._conn.execute(
                    "SELECT * FROM event_log WHERE event_type LIKE ? ORDER BY id DESC LIMIT ?",
                    (f"{event_type}%", limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM event_log ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [
            EventLogEntry(
                id=row["id"],
                timestamp=row["timestamp"],
                event_type=row["event_type"],
                data=json.loads(row["data"]),
            )
            for row in rows
        ]

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/batch/test_event_store.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/devflow/batch/event_store.py tests/unit/batch/test_event_store.py
git commit -m "feat(batch): EventStore — SQLite-backed event history with auto-pruning"
```

---

### Task 2: ApprovalStore SSE publication (approval.waiting / approval.resolved)

**Files:**
- Modify: `src/devflow/daemon/approval_store.py`
- Test: `tests/unit/daemon/test_approval_store.py` (extend)

**Interfaces:**
- Consumes: `EventBus` (optional, passed to `__init__`)
- Produces: `ApprovalStore(event_bus=event_bus)` publishes `approval.waiting` on `register`, `approval.resolved` on `resolve`

- [ ] **Step 1: Write the failing test**

Check if `tests/unit/daemon/test_approval_store.py` exists; if not, create it. Append:

```python
"""Tests for ApprovalStore SSE publication."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from devflow.daemon.approval_store import ApprovalStore
from devflow.daemon.events import EventBus, GLOBAL_TOPIC


def test_register_publishes_approval_waiting() -> None:
    """register() publishes approval.waiting when event_bus is provided."""
    bus = EventBus()
    store = ApprovalStore(event_bus=bus)

    async def _check() -> dict:
        queue = await bus.subscribe(GLOBAL_TOPIC)
        store.register("thread-1", {"gate": "plan_approval", "task_id": "123"})
        return await asyncio.wait_for(queue.get(), timeout=2.0)

    result = asyncio.run(_check())
    assert result["event"] == "approval.waiting"
    assert result["thread_id"] == "thread-1"


def test_resolve_publishes_approval_resolved() -> None:
    """resolve() publishes approval.resolved when event_bus is provided."""
    bus = EventBus()
    store = ApprovalStore(event_bus=bus)
    store.register("thread-1", {"gate": "plan_approval"})

    async def _check() -> dict:
        queue = await bus.subscribe(GLOBAL_TOPIC)
        store.resolve("thread-1", {"approved": True, "reason": "looks good"})
        return await asyncio.wait_for(queue.get(), timeout=2.0)

    result = asyncio.run(_check())
    assert result["event"] == "approval.resolved"
    assert result["thread_id"] == "thread-1"
    assert result["approved"] is True


def test_no_event_bus_does_not_error() -> None:
    """register/resolve without event_bus work as before (no publication)."""
    store = ApprovalStore()  # event_bus=None
    store.register("thread-1", {"gate": "plan_approval"})
    assert store.resolve("thread-1", {"approved": True}) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_approval_store.py -v -k "publishes or no_event_bus"`
Expected: FAIL — `TypeError: ApprovalStore() takes no arguments` or `register does not publish`

- [ ] **Step 3: Modify ApprovalStore**

In `src/devflow/daemon/approval_store.py`:

Change `__init__`:
```python
    def __init__(self, event_bus: Any | None = None) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, _PendingApproval] = {}
        self._event_bus = event_bus
```

Add `_publish_sync` helper method (after `__init__`):
```python
    def _publish_sync(self, data: dict[str, Any]) -> None:
        """Publish an event to EventBus from a synchronous (threading) context.

        Uses ``asyncio.run`` like runner._publish; falls back to a thread
        if already inside a running loop.
        """
        if self._event_bus is None:
            return
        import asyncio

        async def _pub() -> None:
            await self._event_bus.publish("*", data)

        try:
            asyncio.run(_pub())
        except RuntimeError:
            # Already inside a running loop — run in a separate thread.
            import threading
            threading.Thread(target=asyncio.run, args=(_pub(),), daemon=True).start()
```

In `register` (after `logger.info("Registered pending approval for thread %s", thread_id)`):
```python
        self._publish_sync({
            "event": "approval.waiting",
            "thread_id": thread_id,
            **payload,
        })
```

In `resolve` (after `logger.info("Resolved approval for thread %s: approved=%s", thread_id, approved)`):
```python
        self._publish_sync({
            "event": "approval.resolved",
            "thread_id": thread_id,
            "approved": approved,
            "reason": decision.get("reason", ""),
        })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_approval_store.py -v`
Expected: PASS (3 new + existing tests)

- [ ] **Step 5: Commit**

```bash
git add src/devflow/daemon/approval_store.py tests/unit/daemon/test_approval_store.py
git commit -m "feat(daemon): ApprovalStore publishes approval.waiting/resolved SSE events"
```

---

### Task 3: Wire EventStore + SSE subscriber into daemon

**Files:**
- Modify: `src/devflow/daemon/__main__.py`
- Modify: `src/devflow/daemon/web.py` — add `event_store` param to `create_app`
- Modify: `src/devflow/daemon/approval_store.py` — already done in Task 2

**Interfaces:**
- Consumes: `EventStore` (Task 1), `EventBus`, `ApprovalStore(event_bus=...)` (Task 2)
- Produces: `app.state.event_store`, daemon background subscriber task, `ApprovalStore(event_bus=event_bus)`

- [ ] **Step 1: Add event_store param to create_app**

In `src/devflow/daemon/web.py`, add `event_store: Any | None = None` to `create_app` signature (after `queue_store`), and in the state wiring block add:
```python
    app.state.event_store = event_store
```

- [ ] **Step 2: Wire EventStore + subscriber + ApprovalStore in __main__.py**

In `src/devflow/daemon/__main__.py`:

After `queue_store` construction (Task 4 of Enhancements B), add:
```python
    # 3b. Event history store (persistent event log for Activity view).
    from devflow.batch.event_store import EventStore
    event_store = EventStore(str(Path(repo_path) / ".devflow" / "events.db"))
```

Change the `ApprovalStore` construction to pass `event_bus`:
```python
    approval_store = ApprovalStore(event_bus=event_bus)
```

Pass `event_store` to `create_app`:
```python
    app = create_app(
        app_cfg, locks, event_bus, runner,
        approval_store=approval_store, eod_handler=eod_handler,
        scheduler=scheduler, queue_store=queue_store,
        event_store=event_store,
    )
```

Add a background subscriber task that writes events to EventStore. After `scheduler.start()` + `scheduler.register_jobs(repo_path)`, add:
```python
    # 5a. Background task: subscribe to EventBus → write to EventStore.
    import asyncio

    async def _log_events() -> None:
        queue = await event_bus.subscribe(GLOBAL_TOPIC)
        while True:
            try:
                msg = await queue.get()
                event_type = msg.get("event", "unknown")
                await asyncio.to_thread(event_store.add, event_type, msg)
            except Exception:
                logger.exception("EventStore subscriber error")
                await asyncio.sleep(1.0)

    # Run the subscriber in the same loop uvicorn will use.
    # We schedule it as a task on first request via a startup event,
    # or we can use a dedicated thread with its own loop.
    _event_loop_started = False
```

**Important**: uvicorn runs its own event loop. The subscriber task needs to run in that same loop. The cleanest approach: use a FastAPI startup event in `create_app`. Add to `create_app`:
```python
    @app.on_event("startup")
    async def _start_event_logger() -> None:
        if event_store is not None:
            queue = await event_bus.subscribe(GLOBAL_TOPIC)

            async def _log_loop() -> None:
                while True:
                    try:
                        msg = await queue.get()
                        event_type = msg.get("event", "unknown")
                        await asyncio.to_thread(event_store.add, event_type, msg)
                    except Exception:
                        logger.exception("EventStore subscriber error")
                        await asyncio.sleep(1.0)

            asyncio.create_task(_log_loop())
            logger.info("EventStore background subscriber started")
```

Add `event_store.close()` in the daemon `finally` block.

- [ ] **Step 3: Run daemon tests to check for regressions**

Run: `python -m pytest tests/unit/daemon/ -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/devflow/daemon/__main__.py src/devflow/daemon/web.py
git commit -m "feat(daemon): wire EventStore + background EventBus subscriber + approval SSE"
```

---

### Task 4: Backend — `GET /api/events/history` endpoint

**Files:**
- Modify: `src/devflow/daemon/web.py`
- Test: `tests/unit/daemon/test_web_events.py` (new)

**Interfaces:**
- Consumes: `app.state.event_store` (`EventStore.get_recent`)
- Produces: `GET /api/events/history?limit=100&event_type=task`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/daemon/test_web_events.py`:

```python
"""Tests for GET /api/events/history endpoint."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from devflow.batch.event_store import EventStore
from devflow.config import Config, DaemonConfig, WorkflowConfig
from devflow.daemon.events import EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.daemon.web import create_app


def _make_app_with_events(tmp_path: Path) -> tuple:
    store = EventStore(str(tmp_path / "events.db"))
    store.add("task.started", {"event": "task.started", "task_id": "1"})
    store.add("approval.waiting", {"event": "approval.waiting"})
    cfg = Config(workflow=WorkflowConfig(task_source="mock"), providers={}, agents={})
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(
        cfg, locks, bus,
        runner=MagicMock(), scheduler=MagicMock(),
        event_store=store,
    )
    return app, store


def test_get_event_history_returns_entries(tmp_path: Path) -> None:
    app, _ = _make_app_with_events(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/events/history")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    # Newest first.
    assert data[0]["event_type"] == "approval.waiting"


def test_get_event_history_with_filter(tmp_path: Path) -> None:
    app, _ = _make_app_with_events(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/events/history?event_type=task")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["event_type"] == "task.started"


def test_get_event_history_with_limit(tmp_path: Path) -> None:
    app, _ = _make_app_with_events(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/events/history?limit=1")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_get_event_history_no_store_returns_empty(tmp_path: Path) -> None:
    cfg = Config(workflow=WorkflowConfig(task_source="mock"), providers={}, agents={})
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=MagicMock())
    with TestClient(app) as client:
        resp = client.get("/api/events/history")
    assert resp.status_code == 200
    assert resp.json() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_web_events.py -v`
Expected: FAIL — 404

- [ ] **Step 3: Add the endpoint**

In `src/devflow/daemon/web.py`, add inside `create_app` (after the queue endpoints, before `/api/events`):

```python
    @app.get("/api/events/history")
    async def event_history(limit: int = 100, event_type: str | None = None) -> list[dict[str, Any]]:
        es = app.state.event_store  # type: ignore[attr-defined]
        if es is None:
            return []
        return [e.model_dump() for e in es.get_recent(limit=limit, event_type=event_type)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_web_events.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/devflow/daemon/web.py tests/unit/daemon/test_web_events.py
git commit -m "feat(daemon): GET /api/events/history — event log endpoint with filter"
```

---

### Task 5: Frontend — API client + types + activity store + useToast

**Files:**
- Modify: `frontend/src/api/types.ts` — add `EventLogEntry`
- Modify: `frontend/src/api/client.ts` — add `getEventHistory`
- Create: `frontend/src/stores/activity.ts`
- Create: `frontend/src/composables/useToast.ts`

- [ ] **Step 1: Add types**

Append to `frontend/src/api/types.ts`:

```typescript
// --- Event history (P2) ---

export interface EventLogEntry {
  id: number
  timestamp: string
  event_type: string
  data: Record<string, unknown>
}
```

- [ ] **Step 2: Add API client function**

In `frontend/src/api/client.ts`, add import for `EventLogEntry`, then append:

```typescript
// --- Event history (P2) ---
export const getEventHistory = (limit = 100, eventType?: string) =>
  getJson<EventLogEntry[]>(
    `/events/history?limit=${limit}${eventType ? `&event_type=${eventType}` : ''}`,
  )
```

- [ ] **Step 3: Create activity store**

Create `frontend/src/stores/activity.ts`:

```typescript
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getEventHistory } from '@/api/client'
import type { EventLogEntry } from '@/api/types'

export const useActivityStore = defineStore('activity', () => {
  const events = ref<EventLogEntry[]>([])
  const filter = ref<string>('')  // '' = all
  const loading = ref(false)
  const error = ref<string | null>(null)
  const lastUpdated = ref<Date | null>(null)

  async function fetch() {
    loading.value = true
    error.value = null
    try {
      events.value = await getEventHistory(100, filter.value || undefined)
      lastUpdated.value = new Date()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  function setFilter(f: string) {
    filter.value = f
    void fetch()
  }

  /** Prepend a new event (for live SSE updates). */
  function prepend(entry: EventLogEntry) {
    // Avoid duplicates by id.
    if (!events.value.some((e) => e.id === entry.id)) {
      events.value.unshift(entry)
    }
  }

  return { events, filter, loading, error, lastUpdated, fetch, setFilter, prepend }
})
```

- [ ] **Step 4: Create useToast composable**

Create `frontend/src/composables/useToast.ts`:

```typescript
import { ref } from 'vue'

export interface ToastItem {
  id: number
  message: string
  type: 'info' | 'success' | 'warning' | 'error'
}

// Singleton state — shared across all callers.
const toasts = ref<ToastItem[]>([])
let nextId = 0

export function useToast() {
  function show(message: string, type: ToastItem['type'] = 'info') {
    const id = ++nextId
    toasts.value.push({ id, message, type })
    setTimeout(() => dismiss(id), 5000)
  }

  function dismiss(id: number) {
    toasts.value = toasts.value.filter((t) => t.id !== id)
  }

  return { toasts, show, dismiss }
}
```

- [ ] **Step 5: Verify typecheck**

Run: `cd frontend && npm run typecheck`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/api/client.ts frontend/src/stores/activity.ts frontend/src/composables/useToast.ts
git commit -m "feat(frontend): event history API + activity store + useToast composable"
```

---

### Task 6: Frontend — ActivityView + toast-container + SSE + nav + styles

**Files:**
- Create: `frontend/src/views/ActivityView.vue`
- Modify: `frontend/src/App.vue` — nav «Activity» + toast-container
- Modify: `frontend/src/router/index.ts` — `/activity` route
- Modify: `frontend/src/composables/useSSE.ts` — approval.waiting/resolved listeners
- Modify: `frontend/src/style.css` — activity log + toast styles

- [ ] **Step 1: Create ActivityView.vue**

```vue
<script setup lang="ts">
import { computed } from 'vue'
import { useActivityStore } from '@/stores/activity'
import { usePolling } from '@/composables/usePolling'

const activity = useActivityStore()
const { refresh } = usePolling(() => activity.fetch(), 30000)

const filterOptions = [
  { value: '', label: 'Все события' },
  { value: 'task', label: 'Задачи' },
  { value: 'approval', label: 'Approvals' },
  { value: 'queue', label: 'Очередь' },
]

function typeClass(eventType: string): string {
  if (eventType.startsWith('task')) return 'event-task'
  if (eventType.startsWith('approval')) return 'event-approval'
  if (eventType.startsWith('queue')) return 'event-queue'
  if (eventType.includes('error')) return 'event-error'
  return 'event-default'
}

function formatTime(timestamp: string): string {
  const d = new Date(timestamp)
  return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
</script>

<template>
  <section>
    <div class="tasks-header">
      <h2>Activity Log</h2>
      <div class="refresh-section">
        <select :value="activity.filter" @change="activity.setFilter(($event.target as HTMLSelectElement).value)" class="activity-filter">
          <option v-for="opt in filterOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
        </select>
        <button @click="refresh()" :disabled="activity.loading" class="refresh-btn">
          ↻ {{ activity.loading ? 'Загрузка…' : 'Обновить' }}
        </button>
        <small v-if="activity.lastUpdated" class="last-updated">
          Обновлено: {{ activity.lastUpdated.toLocaleTimeString() }}
        </small>
      </div>
    </div>
    <p v-if="activity.error" class="error">{{ activity.error }}</p>
    <ul v-if="activity.events.length" class="activity-list">
      <li v-for="ev in activity.events" :key="ev.id" class="activity-item">
        <span class="activity-time">{{ formatTime(ev.timestamp) }}</span>
        <code :class="typeClass(ev.event_type)">{{ ev.event_type }}</code>
        <span class="activity-data">{{ JSON.stringify(ev.data).slice(0, 120) }}</span>
      </li>
    </ul>
    <div v-else class="empty-state">
      <p class="empty-icon">📭</p>
      <p>Нет событий в журнале.</p>
      <p class="empty-hint">События появятся при работе workflow (запуск задач, approvals, оценка очереди).</p>
    </div>
  </section>
</template>
```

- [ ] **Step 2: Add toast-container to App.vue**

In `frontend/src/App.vue`, add to `<script setup>`:
```typescript
import { useToast } from '@/composables/useToast'
const { toasts, dismiss } = useToast()
```

In the template, after `</main>` and before `</div>` (the `#devflow-app`), add:
```vue
    <div class="toast-container">
      <div
        v-for="t in toasts"
        :key="t.id"
        :class="['toast', `toast-${t.type}`]"
        @click="dismiss(t.id)"
      >
        {{ t.message }}
      </div>
    </div>
```

- [ ] **Step 3: Add Activity nav link + route**

In `frontend/src/router/index.ts`, add before the not-found catch-all:
```typescript
  { path: '/activity', name: 'activity', component: () => import('@/views/ActivityView.vue') },
```

In `frontend/src/App.vue`, add to nav after «Controls»:
```vue
        <span> · </span>
        <RouterLink to="/activity">Activity</RouterLink>
```

- [ ] **Step 4: Add approval SSE listeners to useSSE.ts**

In `frontend/src/composables/useSSE.ts`, add import:
```typescript
import { useToast } from '@/composables/useToast'
import { useActivityStore } from '@/stores/activity'
```

Inside `connect()`, add:
```typescript
  const toast = useToast()
  const activity = useActivityStore()
```

After the `queue.updated` listener, add:
```typescript
    source.addEventListener('approval.waiting', () => {
      toast.show('🔔 Новый approval ожидает решения', 'warning')
      void approvals.fetch()
      void activity.fetch()
    })
    source.addEventListener('approval.resolved', (ev) => {
      try {
        const data = JSON.parse((ev as MessageEvent).data)
        toast.show(
          data.approved ? '✅ Approval approved' : '❌ Approval rejected',
          data.approved ? 'success' : 'error',
        )
      } catch {
        toast.show('Approval resolved', 'info')
      }
      void activity.fetch()
    })
```

Note: need `useApprovalsStore` import for `approvals.fetch()` — check it's already imported. If not, add:
```typescript
import { useApprovalsStore } from '@/stores/approvals'
```
And `const approvals = useApprovalsStore()`.

- [ ] **Step 5: Add styles**

Append to `frontend/src/style.css`:

```css
/* --- Activity log --- */
.activity-list { list-style: none; padding: 0; margin: 0; }
.activity-item {
  display: flex;
  align-items: baseline;
  gap: 0.75rem;
  padding: 0.5rem;
  border-bottom: 1px solid var(--color-border, #e1e1e3);
  font-size: 0.85rem;
}
.activity-time {
  color: #999;
  font-family: var(--font-mono, monospace);
  min-width: 5rem;
}
.activity-data {
  color: #666;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.event-task { color: #0366d6; }
.event-approval { color: #d97706; }
.event-queue { color: #28a745; }
.event-error { color: #cb2431; }
.event-default { color: #666; }
.activity-filter {
  font-size: 0.9rem;
  margin-right: 0.5rem;
}
.empty-state {
  text-align: center;
  padding: 2rem;
  color: #999;
}
.empty-icon { font-size: 2rem; margin-bottom: 0.5rem; }
.empty-hint { font-size: 0.85rem; }

/* --- Toast --- */
.toast-container {
  position: fixed;
  top: 1rem;
  right: 1rem;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.toast {
  padding: 0.75rem 1.25rem;
  border-radius: 4px;
  color: #fff;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(0,0,0,0.2);
  font-size: 0.9rem;
  max-width: 350px;
}
.toast-info { background: #0366d6; }
.toast-success { background: #28a745; }
.toast-warning { background: #d97706; }
.toast-error { background: #cb2431; }
```

- [ ] **Step 6: Verify typecheck + build**

Run: `cd frontend && npm run typecheck && npm run build`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add frontend/src/views/ActivityView.vue frontend/src/App.vue frontend/src/router/index.ts frontend/src/composables/useSSE.ts frontend/src/style.css
git commit -m "feat(frontend): ActivityView + toast-container + approval SSE listeners"
```

---

### Task 7: Smoke test + final verification

**Files:** None (verification only)

- [ ] **Step 1: Run full backend test suite**

Run: `python -m pytest tests/ -q`
Expected: All tests PASS

- [ ] **Step 2: Build frontend**

Run: `cd frontend && npm run build`
Expected: PASS

- [ ] **Step 3: Restart daemon + Playwright smoke test**

Restart daemon. Navigate to `/activity`. Verify:
- Activity Log renders (may be empty)
- Nav has «Activity» link
- `GET /api/events/history` responds

- [ ] **Step 4: Final commit if needed**

---

## Self-Review

**1. Spec coverage:**
- ✅ EventStore SQLite (Section 1) → Task 1
- ✅ Approval SSE register/resolve (Section 2) → Task 2
- ✅ EventStore wiring + subscriber (Section 1) → Task 3
- ✅ GET /api/events/history (Section 3) → Task 4
- ✅ ActivityView + filter + live (Section 3) → Task 6
- ✅ Toast system (Section 3) → Tasks 5, 6
- ✅ approval.waiting/resolved SSE listeners (Section 2) → Task 6

**2. Placeholder scan:** No TBD/TODO. All steps have code. ✅

**3. Type consistency:**
- `EventLogEntry` (id, timestamp, event_type, data) consistent across Tasks 1, 4, 5, 6 ✅
- `EventStore.add(event_type, data)` / `get_recent(limit, event_type)` consistent across Tasks 1, 3, 4 ✅
- `ApprovalStore(event_bus=...)` consistent across Tasks 2, 3 ✅
- `useToast().show(message, type)` consistent across Tasks 5, 6 ✅
