# Dashboard Workflow Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the DevFlow dashboard into a control center — run tasks on demand, edit TODO priorities, manage workflow config, switch HITL strategy, and edit agent prompts — all without daemon restart.

**Architecture:** Hybrid persistence — in-memory mutation of the `Config` object for instant effect, manual save-to-disk (atomic write) for durability. On-demand task runs execute `runner.run_task` in a background thread gated by a `threading.Lock` (not `asyncio.Lock`, which is cross-loop unsafe between APScheduler and uvicorn). New REST endpoints are added to `web.py`; `runner`/`scheduler`/`cfg` are attached to `app.state` so handlers can reach them. A new Vue `ControlsView` with 4 tabs (Run/TODO/Config/Agents) consumes the endpoints via 4 new Pinia stores.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, APScheduler, pytest (backend); Vue 3 Composition API, TypeScript, Pinia, Vue Router, Vite (frontend).

## Global Constraints

- **Daemon is localhost-only** (`127.0.0.1:8787`) — no auth added.
- **`threading.Lock`** for run mutual-exclusion (NOT `asyncio.Lock` — cross-loop unsafe).
- **Atomic writes** for disk persistence: write temp file in same dir, then `os.replace`.
- **YAML** via `yaml.dump(default_flow_style=False, allow_unicode=True, sort_keys=False)` — comments in `workflow.yaml` are lost on Save (accepted limitation).
- **Agent `.md`** via `python-frontmatter` library (`frontmatter.dump`) — already a dependency.
- **Cron validation** via `CronTrigger.from_crontab` in try/except → `422` on invalid.
- **Restart-only fields**: `port`, `serve_frontend`, `frontend_dist` → `422` if sent in PATCH.
- **Backend tests**: pytest in `tests/unit/daemon/`, use `TestClient`, follow existing `_make_app` pattern.
- **Frontend**: no test runner this round; verify via `npm run typecheck` + `npm run build`.
- **Commit after each task** — frequent commits, conventional commits (`feat(daemon):`, `feat(frontend):`, `test(...):`).
- Existing branch: `feature/phase5-vue-dashboard`.

---

## File Structure

**Backend — new:**
- `src/devflow/daemon/todo_api.py` — TODO read/rewrite helpers for web endpoints (JSON serialization, atomic priority/status rewrite).
- `tests/unit/daemon/test_web_controls.py` — endpoint tests for all new control endpoints.
- `tests/unit/daemon/test_scheduler_reschedule.py` — `DaemonScheduler.reschedule` tests.
- `tests/unit/daemon/test_todo_api.py` — `todo_api` helper tests.

**Backend — modified:**
- `src/devflow/daemon/web.py` — new endpoints (run, todo, config, hitl, agents), `scheduler` param, `app.state` wiring.
- `src/devflow/daemon/__main__.py` — pass `scheduler` to `create_app`.
- `src/devflow/daemon/scheduler.py` — add `reschedule()` and `set_eod_job(enabled)`.

**Frontend — new:**
- `frontend/src/api/helpers.ts` — `patchJson`, `putJson` HTTP helpers (extend existing client).
- `frontend/src/stores/controls.ts`, `config.ts`, `todo.ts`, `agents.ts` — 4 new Pinia stores.
- `frontend/src/views/ControlsView.vue` — tab container.
- `frontend/src/components/controls/RunTab.vue`, `TodoTab.vue`, `ConfigTab.vue`, `AgentsTab.vue` — 4 tab components.

**Frontend — modified:**
- `frontend/src/api/client.ts` — add control endpoint functions.
- `frontend/src/api/types.ts` — add control types.
- `frontend/src/App.vue` — add «Controls» nav link.
- `frontend/src/router/index.ts` — add `/controls` route.
- `frontend/src/composables/useSSE.ts` — refresh `controlsStore` on task events.
- `frontend/src/style.css` — minor additions for tabs, priority colors, badges.

---

### Task 1: Add `reschedule()` and `set_eod_job()` to DaemonScheduler

**Files:**
- Modify: `src/devflow/daemon/scheduler.py:71-109` (add methods after `register_jobs`)
- Test: `tests/unit/daemon/test_scheduler_reschedule.py` (new)

**Interfaces:**
- Consumes: `CronTrigger.from_crontab` (apscheduler), existing `self._scheduler`, `self._lock`
- Produces:
  - `DaemonScheduler.reschedule(task_schedule: str | None = None, eod_schedule: str | None = None) -> None`
  - `DaemonScheduler.set_eod_job(enabled: bool, repo_path: str = ".") -> None`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for DaemonScheduler.reschedule and set_eod_job."""

from __future__ import annotations

from apscheduler.triggers.cron import CronTrigger

from devflow.config import Config, HitlStrategy, WorkflowConfig
from devflow.daemon.events import EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.daemon.runner import WorkflowRunner
from devflow.daemon.scheduler import DaemonScheduler


def _make_scheduler(strategy: str = HitlStrategy.PER_PLAN) -> DaemonScheduler:
    cfg = Config(
        workflow=WorkflowConfig(task_source="mock", hitl_strategy=strategy),
        providers={},
        agents={},
    )
    bus = EventBus()
    locks = DaemonLocks()
    runner = WorkflowRunner(cfg, bus, locks)
    sched = DaemonScheduler(cfg, runner, eod_handler=None)
    sched.start()
    sched.register_jobs(".")
    return sched


def test_reschedule_task_run_updates_trigger() -> None:
    sched = _make_scheduler()
    try:
        sched.reschedule(task_schedule="*/30 * * * *")
        job = sched._scheduler.get_job("task_run")
        assert job is not None
        # Next run time reflects the new 30-min schedule.
        assert job.trigger is not None
    finally:
        sched.shutdown()


def test_reschedule_invalid_cron_raises() -> None:
    import pytest
    sched = _make_scheduler()
    try:
        with pytest.raises(ValueError):
            sched.reschedule(task_schedule="not a cron")
    finally:
        sched.shutdown()


def test_set_eod_job_enables_when_end_of_day() -> None:
    sched = _make_scheduler(strategy=HitlStrategy.PER_PLAN)
    try:
        # Initially no eod_review job (per_plan strategy).
        assert sched._scheduler.get_job("eod_review") is None
        sched.set_eod_job(enabled=True, repo_path=".")
        assert sched._scheduler.get_job("eod_review") is not None
    finally:
        sched.shutdown()


def test_set_eod_job_disables_when_switching_away() -> None:
    sched = _make_scheduler(strategy=HitlStrategy.END_OF_DAY)
    try:
        assert sched._scheduler.get_job("eod_review") is not None
        sched.set_eod_job(enabled=False, repo_path=".")
        assert sched._scheduler.get_job("eod_review") is None
    finally:
        sched.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_scheduler_reschedule.py -v`
Expected: FAIL — `AttributeError: 'DaemonScheduler' object has no attribute 'reschedule'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/devflow/daemon/scheduler.py` after the `register_jobs` method (after line 109):

```python
    def reschedule(
        self,
        task_schedule: str | None = None,
        eod_schedule: str | None = None,
    ) -> None:
        """Reschedule cron jobs to new schedules.

        Validates each cron string via :class:`CronTrigger.from_crontab`
        (raises ``ValueError`` on invalid syntax). Only re-registers jobs
        that currently exist; safe to call before ``register_jobs``.
        """
        with self._lock:
            if task_schedule is not None:
                trigger = CronTrigger.from_crontab(task_schedule)  # raises ValueError
                if self._scheduler.get_job("task_run") is not None:
                    self._scheduler.reschedule_job(
                        "task_run", trigger=trigger
                    )
                self._cfg.workflow.daemon.task_schedule = task_schedule
                logger.info("Rescheduled task_run to: %s", task_schedule)

            if eod_schedule is not None:
                trigger = CronTrigger.from_crontab(eod_schedule)  # raises ValueError
                if self._scheduler.get_job("eod_review") is not None:
                    self._scheduler.reschedule_job(
                        "eod_review", trigger=trigger
                    )
                self._cfg.workflow.daemon.eod_schedule = eod_schedule
                logger.info("Rescheduled eod_review to: %s", eod_schedule)

    def set_eod_job(self, enabled: bool, repo_path: str = ".") -> None:
        """Enable or disable the EOD review cron job at runtime.

        Called when HITL strategy is switched to/from ``end_of_day`` so the
        EOD job matches the current strategy without a daemon restart.
        """
        with self._lock:
            if enabled:
                if self._scheduler.get_job("eod_review") is None:
                    eod_trigger = CronTrigger.from_crontab(
                        self._cfg.workflow.daemon.eod_schedule
                    )
                    self._scheduler.add_job(
                        self._run_eod_wrapper,
                        trigger=eod_trigger,
                        id="eod_review",
                        max_instances=1,
                        coalesce=True,
                        kwargs={"repo_path": repo_path},
                        replace_existing=True,
                    )
                    logger.info("Enabled eod_review job")
            else:
                if self._scheduler.get_job("eod_review") is not None:
                    self._scheduler.remove_job("eod_review")
                    logger.info("Disabled eod_review job")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_scheduler_reschedule.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/devflow/daemon/scheduler.py tests/unit/daemon/test_scheduler_reschedule.py
git commit -m "feat(daemon): add scheduler.reschedule + set_eod_job for runtime control"
```

---

### Task 2: Create `todo_api.py` helpers (read + atomic line rewrite)

**Files:**
- Create: `src/devflow/daemon/todo_api.py`
- Test: `tests/unit/daemon/test_todo_api.py` (new)

**Interfaces:**
- Consumes: `devflow.todo.parse_todo` (returns `list[TodoItem]`), `devflow.state` checkbox constants
- Produces:
  - `serialize_todo(items: list[TodoItem]) -> list[dict[str, Any]]` — JSON-serializable TODO entries
  - `rewrite_todo_line(path: Path, line_no: int, *, priority: int | None = None, status: str | None = None) -> dict[str, Any]` — atomic single-line update; returns the updated serialized entry

- [ ] **Step 1: Write the failing test**

```python
"""Tests for todo_api helpers (web-facing TODO read/rewrite)."""

from __future__ import annotations

from pathlib import Path

from devflow.daemon.todo_api import rewrite_todo_line, serialize_todo
from devflow.todo import parse_todo

_TODO_CONTENT = """\
# TODO

- [ ] #r2 [#251977](https://example.com/251977) — Fix the bug
- [ ] #r1 — Urgent fix
- [~] #r3 — In progress task
- [x] #r4 — Done task
"""


def _write_todo(tmp_path: Path) -> Path:
    path = tmp_path / "TODO.md"
    path.write_text(_TODO_CONTENT, encoding="utf-8")
    return path


def test_serialize_todo_returns_json_entries(tmp_path: Path) -> None:
    path = _write_todo(tmp_path)
    items = parse_todo(path)
    data = serialize_todo(items)
    # First line is a heading (non-task), second is the task with priority 2.
    assert data[0]["line_no"] == 1
    assert data[0]["checkbox"] is None
    assert data[1]["line_no"] == 2
    assert data[1]["checkbox"] == "[ ]"
    assert data[1]["priority"] == 2
    assert data[1]["task_ref"] == "251977"


def test_rewrite_todo_line_changes_priority(tmp_path: Path) -> None:
    path = _write_todo(tmp_path)
    result = rewrite_todo_line(path, line_no=2, priority=0)
    assert result["priority"] == 0
    # Re-read from disk to confirm persistence.
    items = parse_todo(path)
    assert items[1].priority == 0


def test_rewrite_todo_line_changes_status(tmp_path: Path) -> None:
    path = _write_todo(tmp_path)
    result = rewrite_todo_line(path, line_no=2, status="in_progress")
    assert result["checkbox"] == "[~]"
    items = parse_todo(path)
    assert items[1].checkbox == "[~]"


def test_rewrite_todo_line_missing_line_raises(tmp_path: Path) -> None:
    import pytest

    path = _write_todo(tmp_path)
    with pytest.raises(ValueError, match="not found"):
        rewrite_todo_line(path, line_no=999, priority=0)


def test_rewrite_todo_line_invalid_priority_raises(tmp_path: Path) -> None:
    import pytest

    path = _write_todo(tmp_path)
    with pytest.raises(ValueError, match="priority"):
        rewrite_todo_line(path, line_no=2, priority=7)


def test_rewrite_todo_line_non_task_line_raises(tmp_path: Path) -> None:
    import pytest

    path = _write_todo(tmp_path)
    # Line 1 is the heading "# TODO" — not a task.
    with pytest.raises(ValueError, match="not a task"):
        rewrite_todo_line(path, line_no=1, priority=0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_todo_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'devflow.daemon.todo_api'`

- [ ] **Step 3: Write minimal implementation**

Create `src/devflow/daemon/todo_api.py`:

```python
"""Web-facing helpers for reading and rewriting TODO.md entries.

Used by the ``/api/todo`` endpoints. The orchestrator re-reads TODO.md from
disk on every run, so editing the file is sufficient for changes to take
effect — no in-memory daemon state needs invalidation.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

from devflow.state import (
    CHECKBOX_DONE,
    CHECKBOX_IN_PROGRESS,
    CHECKBOX_OPEN,
    TodoItem,
)
from devflow.todo import PRIORITY_RE, parse_todo

# Maps the web-facing status string to the checkbox marker.
_STATUS_TO_CHECKBOX: dict[str, str] = {
    "open": CHECKBOX_OPEN,
    "in_progress": CHECKBOX_IN_PROGRESS,
    "done": CHECKBOX_DONE,
}


def serialize_todo(items: list[TodoItem]) -> list[dict[str, Any]]:
    """Serialize parsed TODO items into JSON-friendly dicts."""
    return [
        {
            "line_no": item.line_no,
            "text": item.raw_line,
            "checkbox": item.checkbox,
            "priority": item.priority,
            "task_ref": item.task_ref,
            "url": item.url,
            "title": item.title,
        }
        for item in items
    ]


def _replace_priority(raw: str, new_priority: int) -> str:
    """Swap or insert a #rX priority tag in a raw line."""
    if PRIORITY_RE.search(raw):
        return PRIORITY_RE.sub(f"#r{new_priority}", raw, count=1)
    # No existing tag: insert after the checkbox marker.
    return re.sub(r"^(\s*[-*]\s+\[[ |~x]\])", rf"\1 #r{new_priority}", raw, count=1)


def _replace_checkbox(raw: str, new_checkbox: str) -> str:
    """Swap the checkbox marker in a raw line."""
    for marker in (CHECKBOX_OPEN, CHECKBOX_IN_PROGRESS, CHECKBOX_DONE):
        if marker in raw:
            return raw.replace(marker, new_checkbox, 1)
    return raw


def _atomic_write(path: Path, content: str) -> None:
    """Write content atomically: temp file in same dir + os.replace."""
    dir_ = path.parent
    fd, tmp_name = tempfile.mkstemp(dir=str(dir_), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_name, path)
    except Exception:
        # Clean up the temp file on failure.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def rewrite_todo_line(
    path: Path,
    line_no: int,
    *,
    priority: int | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Atomically rewrite a single TODO.md line's priority and/or status.

    Returns the updated serialized entry. Raises ``ValueError`` if the line
    does not exist, is not a task line, or the priority/status is invalid.
    """
    if priority is not None and not (0 <= priority <= 5):
        raise ValueError(f"Invalid priority {priority}: must be 0..5")
    if status is not None and status not in _STATUS_TO_CHECKBOX:
        raise ValueError(f"Invalid status {status!r}")

    items = parse_todo(path)
    target = next((it for it in items if it.line_no == line_no), None)
    if target is None:
        raise ValueError(f"TODO line {line_no} not found")
    if not target.is_task:
        raise ValueError(f"TODO line {line_no} is not a task line")

    new_line = target.raw_line
    if priority is not None:
        new_line = _replace_priority(new_line, priority)
    if status is not None:
        new_line = _replace_checkbox(new_line, _STATUS_TO_CHECKBOX[status])

    # Rewrite the whole file with the single updated line (atomic).
    lines = path.read_text(encoding="utf-8").splitlines()
    idx = line_no - 1
    if 0 <= idx < len(lines):
        lines[idx] = new_line
    _atomic_write(path, "\n".join(lines) + "\n")

    # Re-parse to return the updated entry with fresh fields.
    updated = parse_todo(path)
    return serialize_todo([it for it in updated if it.line_no == line_no])[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_todo_api.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/devflow/daemon/todo_api.py tests/unit/daemon/test_todo_api.py
git commit -m "feat(daemon): add todo_api helpers for web TODO read/rewrite"
```

---

### Task 3: Wire `app.state` + `scheduler` param into `create_app`

**Files:**
- Modify: `src/devflow/daemon/web.py:79-103` (signature + state wiring)
- Modify: `src/devflow/daemon/__main__.py:94-97` (pass scheduler)
- Test: `tests/unit/daemon/test_web_controls.py` (new — start with wiring test only)

**Interfaces:**
- Consumes: `WorkflowRunner` (`runner.py`), `DaemonScheduler` (`scheduler.py`), `Config` (`config.py`)
- Produces: `app.state.runner`, `app.state.scheduler`, `app.state.cfg`, `app.state._run_lock` (threading.Lock)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/daemon/test_web_controls.py`:

```python
"""Tests for dashboard control endpoints (run tasks, todo, config, agents)."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from devflow.config import Config, DaemonConfig, WorkflowConfig
from devflow.daemon.events import EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.daemon.web import create_app


def _make_control_app(
    cfg: Config | None = None,
    runner: MagicMock | None = None,
    scheduler: MagicMock | None = None,
) -> tuple:
    """Create an app wired with runner + scheduler for control endpoints."""
    if cfg is None:
        cfg = Config(
            workflow=WorkflowConfig(task_source="mock"),
            providers={},
            agents={
                "planner": MagicMock(
                    name="planner",
                    provider="mock",
                    model="mock-model",
                    temperature=0.3,
                    system_prompt="You are the planner.",
                ),
            },
        )
        cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(
        cfg, locks, bus,
        runner=runner or MagicMock(),
        scheduler=scheduler or MagicMock(),
    )
    return app, cfg


def test_app_state_exposes_runner_scheduler_cfg() -> None:
    """app.state has runner, scheduler, cfg, _run_lock attached."""
    app, cfg = _make_control_app()
    assert app.state.runner is not None
    assert app.state.scheduler is not None
    assert app.state.cfg is cfg
    assert isinstance(app.state._run_lock, type(threading.Lock()))


def test_create_app_accepts_scheduler_param() -> None:
    """create_app signature accepts scheduler=None without error."""
    cfg = Config(workflow=WorkflowConfig(task_source="mock"), providers={}, agents={})
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    # scheduler=None must not raise.
    app = create_app(cfg, locks, bus, runner=None, scheduler=None)
    assert app.state.scheduler is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_web_controls.py -v`
Expected: FAIL — `TypeError: create_app() got an unexpected keyword argument 'scheduler'`

- [ ] **Step 3: Write minimal implementation**

In `src/devflow/daemon/web.py`, modify the `create_app` signature (line 79-86) to add `scheduler` and wire `app.state`. Add `import threading` to the imports at top.

Change the signature from:
```python
def create_app(
    app_cfg: Config,
    locks: DaemonLocks,
    event_bus: EventBus,
    runner: Any | None = None,
    approval_store: ApprovalStore | None = None,
    eod_handler: EodHandler | None = None,
) -> FastAPI:
```
to:
```python
def create_app(
    app_cfg: Config,
    locks: DaemonLocks,
    event_bus: EventBus,
    runner: Any | None = None,
    approval_store: ApprovalStore | None = None,
    eod_handler: EodHandler | None = None,
    scheduler: Any | None = None,
) -> FastAPI:
```

Then right after `start_time = time.monotonic()` and `_state: dict[str, Any] = ...` (line 102-103), add:

```python
    # Expose runner/scheduler/cfg on app.state so control endpoints can reach them.
    app.state.runner = runner
    app.state.scheduler = scheduler
    app.state.cfg = app_cfg
    app.state._run_lock = threading.Lock()  # cross-loop-safe (threading, not asyncio)
```

Add `import threading` to the imports at the top of `web.py` (after `import time`).

In `src/devflow/daemon/__main__.py`, change line 94-97 from:
```python
    app = create_app(
        app_cfg, locks, event_bus, runner,
        approval_store=approval_store, eod_handler=eod_handler,
    )
```
to:
```python
    app = create_app(
        app_cfg, locks, event_bus, runner,
        approval_store=approval_store, eod_handler=eod_handler,
        scheduler=scheduler,
    )
```
**Note:** `scheduler` is created on line 101, AFTER this `create_app` call. Move the `create_app` call to AFTER scheduler creation. Reorder: move lines 100-103 (scheduler creation + start + register_jobs) BEFORE the `create_app` call (line 94). Keep `runner._on_task_change = app.state.set_current_task` after `create_app`.

The reordered block (lines 87-103) becomes:
```python
    runner = WorkflowRunner(
        app_cfg, event_bus, locks, approval_bridge=bridge, batch_store=batch_store
    )

    # 4. Create and start scheduler, register jobs.
    scheduler = DaemonScheduler(app_cfg, runner, eod_handler=eod_handler)
    scheduler.start()
    scheduler.register_jobs(repo_path)

    # Build the app explicitly so we can wire the current-task callback.
    # The callback lets /api/health and /api/tasks/current reflect the task
    # the runner is actively working on (set on run start, cleared on end).
    app = create_app(
        app_cfg, locks, event_bus, runner,
        approval_store=approval_store, eod_handler=eod_handler,
        scheduler=scheduler,
    )
    runner._on_task_change = app.state.set_current_task  # type: ignore[attr-defined]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_web_controls.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run existing daemon tests to confirm no regression**

Run: `python -m pytest tests/unit/daemon/ -v`
Expected: PASS (all existing + new tests)

- [ ] **Step 6: Commit**

```bash
git add src/devflow/daemon/web.py src/devflow/daemon/__main__.py tests/unit/daemon/test_web_controls.py
git commit -m "feat(daemon): wire app.state (runner/scheduler/cfg) + threading.Lock for control"
```

---

### Task 4: `POST /api/tasks/run` endpoint (on-demand task run)

**Files:**
- Modify: `src/devflow/daemon/web.py` (add endpoint after `/api/tasks/done` ~line 195)
- Test: `tests/unit/daemon/test_web_controls.py` (append run tests)

**Interfaces:**
- Consumes: `app.state.runner` (`runner.run_task(task_id, repo_path)` / `runner.run_all(repo_path)`), `app.state._run_lock`
- Produces: `POST /api/tasks/run` — body `{task_id?: str, repo_path?: str}` → `202 {run_id, task_id?, status}` or `409`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/daemon/test_web_controls.py`:

```python
import time


def test_run_task_returns_202_with_run_id() -> None:
    """POST /api/tasks/run returns 202 with a run_id when idle."""
    runner = MagicMock()
    runner.run_task.return_value = {}
    app, _ = _make_control_app(runner=runner)
    with TestClient(app) as client:
        resp = client.post("/api/tasks/run", json={"task_id": "12345"})
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "started"
    assert "run_id" in data
    assert data["task_id"] == "12345"


def test_run_task_returns_409_when_busy() -> None:
    """POST /api/tasks/run returns 409 when _run_lock is held."""
    runner = MagicMock()
    app, _ = _make_control_app(runner=runner)
    with TestClient(app) as client:
        # Hold the lock to simulate a running task.
        app.state._run_lock.acquire()
        try:
            resp = client.post("/api/tasks/run", json={"task_id": "12345"})
        finally:
            app.state._run_lock.release()
    assert resp.status_code == 409
    assert "already running" in resp.json()["detail"].lower()


def test_run_task_runs_in_background_thread() -> None:
    """The handler returns 202 immediately without blocking on run_task."""
    started = threading.Event()

    def slow_run(task_id, repo_path, thread_id=None):
        started.set()
        time.sleep(0.2)
        return {}

    runner = MagicMock()
    runner.run_task.side_effect = slow_run
    app, _ = _make_control_app(runner=runner)
    with TestClient(app) as client:
        resp = client.post("/api/tasks/run", json={"task_id": "12345"})
        # Response arrives before the slow run finishes.
        assert resp.status_code == 202
        # But the run eventually starts in the background.
        assert started.wait(timeout=2.0)


def test_run_task_without_task_id_calls_run_all() -> None:
    """No task_id → runner.run_all is used instead of run_task."""
    runner = MagicMock()
    runner.run_all.return_value = []
    app, _ = _make_control_app(runner=runner)
    with TestClient(app) as client:
        resp = client.post("/api/tasks/run", json={})
    assert resp.status_code == 202
    runner.run_all.assert_called_once()
    runner.run_task.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_web_controls.py -k run_task -v`
Expected: FAIL — `404 Not Found` (endpoint doesn't exist yet)

- [ ] **Step 3: Write minimal implementation**

In `src/devflow/daemon/web.py`, add `import uuid` to imports, then add a Pydantic model near the other models (after `EodPublishRequest`, ~line 63):

```python
class RunTaskRequest(BaseModel):
    """Body of POST /api/tasks/run."""

    task_id: str | None = None
    repo_path: str | None = None


class RunTaskResponse(BaseModel):
    """Response of POST /api/tasks/run."""

    run_id: str
    task_id: str | None = None
    status: str
```

Then add the endpoint inside `create_app`, after the `task_detail` route (~line 205, before `/api/events`):

```python
    @app.post("/api/tasks/run", response_model=RunTaskResponse, status_code=202)
    async def run_task(req: RunTaskRequest) -> RunTaskResponse:
        """Trigger a workflow run on demand.

        If ``task_id`` is provided, runs that specific task; otherwise runs
        the next task(s) by priority (``runner.run_all``). The run executes
        in a background thread so the response returns immediately.
        Returns ``409`` if a task is already running.
        """
        import asyncio

        run_lock = app.state._run_lock  # type: ignore[attr-defined]
        runner = app.state.runner  # type: ignore[attr-defined]
        if runner is None:
            raise HTTPException(status_code=503, detail="No runner configured")
        if not run_lock.acquire(blocking=False):
            current = _state.get("current_task")
            raise HTTPException(
                status_code=409,
                detail=f"Task already running: {current or 'unknown'}",
            )

        repo = req.repo_path or "."
        run_id = str(uuid.uuid4())

        async def _run_in_background() -> None:
            try:
                await asyncio.to_thread(
                    _execute_run, runner, req.task_id, repo
                )
            finally:
                run_lock.release()

        asyncio.create_task(_run_in_background())
        return RunTaskResponse(
            run_id=run_id, task_id=req.task_id, status="started"
        )
```

Add a module-level helper function (outside `create_app`, after the models):

```python
def _execute_run(runner: Any, task_id: str | None, repo_path: str) -> None:
    """Synchronous run wrapper called in a background thread.

    Calls ``runner.run_task`` (specific task) or ``runner.run_all`` (next
    by priority). Exceptions are logged; the lock is released by the caller.
    """
    try:
        if task_id:
            runner.run_task(task_id=task_id, repo_path=repo_path)
        else:
            runner.run_all(repo_path=repo_path)
    except Exception:
        logger.exception("On-demand run failed (task_id=%s)", task_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_web_controls.py -k run_task -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/devflow/daemon/web.py tests/unit/daemon/test_web_controls.py
git commit -m "feat(daemon): POST /api/tasks/run — on-demand task execution"
```

---

### Task 5: `GET/PATCH /api/todo` endpoints

**Files:**
- Modify: `src/devflow/daemon/web.py` (add endpoints)
- Test: `tests/unit/daemon/test_web_controls.py` (append todo tests)

**Interfaces:**
- Consumes: `devflow.daemon.todo_api.serialize_todo`, `rewrite_todo_line`, `app.state.cfg.workflow.todo_path`
- Produces: `GET /api/todo`, `PATCH /api/todo/{line_no}`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/daemon/test_web_controls.py`:

```python
_TODO = """\
# TODO

- [ ] #r2 [#251977](https://example.com/251977) — Fix the bug
- [ ] #r1 — Urgent fix
"""


def _make_app_with_todo(tmp_path: Path) -> tuple:
    todo_path = tmp_path / "TODO.md"
    todo_path.write_text(_TODO, encoding="utf-8")
    cfg = Config(
        workflow=WorkflowConfig(task_source="mock", todo_path=str(todo_path)),
        providers={},
        agents={},
    )
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=MagicMock())
    return app, todo_path


def test_get_todo_returns_entries(tmp_path: Path) -> None:
    app, _ = _make_app_with_todo(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/todo")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    assert data[1]["checkbox"] == "[ ]"
    assert data[1]["priority"] == 2


def test_patch_todo_changes_priority(tmp_path: Path) -> None:
    app, todo_path = _make_app_with_todo(tmp_path)
    with TestClient(app) as client:
        resp = client.patch("/api/todo/2", json={"priority": 0})
    assert resp.status_code == 200
    assert resp.json()["priority"] == 0
    # Persisted to disk.
    assert "#r0" in todo_path.read_text(encoding="utf-8")


def test_patch_todo_changes_status(tmp_path: Path) -> None:
    app, _ = _make_app_with_todo(tmp_path)
    with TestClient(app) as client:
        resp = client.patch("/api/todo/2", json={"status": "done"})
    assert resp.status_code == 200
    assert resp.json()["checkbox"] == "[x]"


def test_patch_todo_missing_line_404(tmp_path: Path) -> None:
    app, _ = _make_app_with_todo(tmp_path)
    with TestClient(app) as client:
        resp = client.patch("/api/todo/999", json={"priority": 0})
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_web_controls.py -k todo -v`
Expected: FAIL — `404 Not Found` (endpoints don't exist)

- [ ] **Step 3: Write minimal implementation**

In `src/devflow/daemon/web.py`, add import at top:
```python
from devflow.daemon.todo_api import rewrite_todo_line, serialize_todo
from devflow.todo import parse_todo
from pathlib import Path
```
(Add `from pathlib import Path` to imports if not present — check; it may already be imported via other modules. Actually `web.py` doesn't import Path currently. Add it.)

Add Pydantic models near the other models:
```python
class TodoPatchRequest(BaseModel):
    """Body of PATCH /api/todo/{line_no}."""

    priority: int | None = None
    status: str | None = None
```

Add endpoints inside `create_app` (after the run_task endpoint, before `/api/events`):

```python
    @app.get("/api/todo")
    async def list_todo() -> list[dict[str, Any]]:
        """List all TODO.md entries (re-read from disk each call)."""
        todo_path = Path(app_cfg.workflow.todo_path)
        items = parse_todo(todo_path)
        return serialize_todo(items)

    @app.patch("/api/todo/{line_no}")
    async def patch_todo(line_no: int, req: TodoPatchRequest) -> dict[str, Any]:
        """Update a single TODO line's priority and/or status (atomic disk write)."""
        todo_path = Path(app_cfg.workflow.todo_path)
        try:
            return rewrite_todo_line(
                todo_path, line_no,
                priority=req.priority, status=req.status,
            )
        except ValueError as exc:
            msg = str(exc)
            if "not found" in msg or "not a task" in msg:
                raise HTTPException(status_code=404, detail=msg) from exc
            raise HTTPException(status_code=422, detail=msg) from exc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_web_controls.py -k todo -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/devflow/daemon/web.py tests/unit/daemon/test_web_controls.py
git commit -m "feat(daemon): GET/PATCH /api/todo — read and edit TODO priorities"
```

---

### Task 6: `GET/PATCH /api/config` + `/api/config/diff` + `/api/config/save` endpoints

**Files:**
- Modify: `src/devflow/daemon/web.py` (add endpoints)
- Test: `tests/unit/daemon/test_web_controls.py` (append config tests)

**Interfaces:**
- Consumes: `app.state.cfg`, `app.state.scheduler` (`reschedule`, `set_eod_job`), `yaml.dump`, `os.replace`
- Produces: `GET /api/config`, `PATCH /api/config`, `GET /api/config/diff`, `POST /api/config/save`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/daemon/test_web_controls.py`:

```python
def test_get_config_returns_all_fields(tmp_path: Path) -> None:
    cfg = Config(
        workflow=WorkflowConfig(task_source="mock", hitl_strategy="per_plan"),
        providers={},
        agents={},
    )
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=MagicMock())
    with TestClient(app) as client:
        resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["hitl_strategy"] == "per_plan"
    assert data["daemon"]["task_schedule"] == "0 9,15 * * 1-5"
    assert data["daemon"]["port"] == 8787


def test_patch_config_mutates_hitl_in_memory(tmp_path: Path) -> None:
    cfg = Config(
        workflow=WorkflowConfig(task_source="mock"),
        providers={},
        agents={},
    )
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    sched = MagicMock()
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=sched)
    with TestClient(app) as client:
        resp = client.patch("/api/config", json={"hitl_strategy": "full_detail"})
    assert resp.status_code == 200
    assert cfg.workflow.hitl_strategy == "full_detail"


def test_patch_config_rejects_restart_only_field(tmp_path: Path) -> None:
    cfg = Config(workflow=WorkflowConfig(task_source="mock"), providers={}, agents={})
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=MagicMock())
    with TestClient(app) as client:
        resp = client.patch("/api/config", json={"daemon": {"port": 9999}})
    assert resp.status_code == 422
    assert "restart" in resp.json()["detail"].lower()


def test_patch_config_reschedules_on_schedule_change(tmp_path: Path) -> None:
    cfg = Config(workflow=WorkflowConfig(task_source="mock"), providers={}, agents={})
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    sched = MagicMock()
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=sched)
    with TestClient(app) as client:
        resp = client.patch(
            "/api/config",
            json={"daemon": {"task_schedule": "*/30 * * * *"}},
        )
    assert resp.status_code == 200
    sched.reschedule.assert_called_once()
    assert cfg.workflow.daemon.task_schedule == "*/30 * * * *"


def test_patch_config_invalid_cron_returns_422(tmp_path: Path) -> None:
    cfg = Config(workflow=WorkflowConfig(task_source="mock"), providers={}, agents={})
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    sched = MagicMock()
    sched.reschedule.side_effect = ValueError("bad cron")
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=sched)
    with TestClient(app) as client:
        resp = client.patch(
            "/api/config",
            json={"daemon": {"task_schedule": "bad"}},
        )
    assert resp.status_code == 422


def test_save_config_writes_yaml_to_disk(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    wf_path = config_dir / "workflow.yaml"
    cfg = Config(
        workflow=WorkflowConfig(task_source="mock", hitl_strategy="full_detail"),
        providers={},
        agents={},
    )
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=MagicMock())
    # Patch the config_dir attribute onto cfg for the save endpoint.
    app.state.config_dir = str(config_dir)
    with TestClient(app) as client:
        resp = client.post("/api/config/save")
    assert resp.status_code == 200
    assert wf_path.exists()
    written = wf_path.read_text(encoding="utf-8")
    assert "full_detail" in written


def test_config_diff_shows_unsaved_changes(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "workflow.yaml").write_text(
        "task_source: mock\nhitl_strategy: per_plan\n", encoding="utf-8"
    )
    cfg = Config(
        workflow=WorkflowConfig(task_source="mock", hitl_strategy="full_detail"),
        providers={},
        agents={},
    )
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=MagicMock())
    app.state.config_dir = str(config_dir)
    with TestClient(app) as client:
        resp = client.get("/api/config/diff")
    assert resp.status_code == 200
    data = resp.json()
    assert data["clean"] is False
    changed_fields = [c["field"] for c in data["changed"]]
    assert "hitl_strategy" in changed_fields
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_web_controls.py -k config -v`
Expected: FAIL — `404 Not Found`

- [ ] **Step 3: Write minimal implementation**

In `src/devflow/daemon/web.py`, add `import yaml` and `import tempfile` and `import os` to imports (check which are present — `os` and `json` already imported; add `yaml`, `tempfile`).

Add Pydantic models:
```python
class ConfigPatchRequest(BaseModel):
    """Body of PATCH /api/config. All fields optional."""

    hitl_strategy: str | None = None
    daemon: dict[str, Any] | None = None
```

Add the `_RESTART_ONLY_DAEMON_FIELDS` constant near the top of `create_app` (after `_state`):
```python
    _RESTART_ONLY_DAEMON_FIELDS = {"port", "serve_frontend", "frontend_dist"}
```

Add endpoints inside `create_app` (after todo endpoints, before `/api/events`):

```python
    def _config_view() -> dict[str, Any]:
        """Serialize the current in-memory config into a JSON view."""
        d = app_cfg.workflow.daemon
        return {
            "task_source": app_cfg.workflow.task_source,
            "hitl_strategy": app_cfg.workflow.hitl_strategy,
            "todo_path": app_cfg.workflow.todo_path,
            "human_in_the_loop": app_cfg.workflow.human_in_the_loop,
            "daemon": {
                "enabled": d.enabled,
                "task_schedule": d.task_schedule,
                "eod_schedule": d.eod_schedule,
                "port": d.port,
                "approval_timeout_hours": d.approval_timeout_hours,
                "approval_on_timeout": d.approval_on_timeout,
                "serve_frontend": d.serve_frontend,
                "frontend_dist": d.frontend_dist,
            },
            "forge": {
                "provider": app_cfg.workflow.forge.provider,
                "target_branch": app_cfg.workflow.forge.target_branch,
                "actions": app_cfg.workflow.forge.actions,
            },
        }

    @app.get("/api/config")
    async def get_config() -> dict[str, Any]:
        return _config_view()

    @app.patch("/api/config")
    async def patch_config(req: ConfigPatchRequest) -> dict[str, Any]:
        """Mutate in-memory config. Schedule changes trigger scheduler.reschedule."""
        if req.hitl_strategy is not None:
            if req.hitl_strategy not in HitlStrategy.ALL:
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid hitl_strategy: {req.hitl_strategy}",
                )
            app_cfg.workflow.hitl_strategy = req.hitl_strategy

        if req.daemon:
            for key, val in req.daemon.items():
                if key in _RESTART_ONLY_DAEMON_FIELDS:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Field '{key}' requires daemon restart",
                    )
            # Apply mutable daemon fields.
            sched_obj = app.state.scheduler
            if "task_schedule" in req.daemon or "eod_schedule" in req.daemon:
                if sched_obj is not None:
                    try:
                        sched_obj.reschedule(
                            task_schedule=req.daemon.get("task_schedule"),
                            eod_schedule=req.daemon.get("eod_schedule"),
                        )
                    except ValueError as exc:
                        raise HTTPException(
                            status_code=422, detail=str(exc)
                        ) from exc
                else:
                    # No scheduler: just mutate the config field.
                    if "task_schedule" in req.daemon:
                        app_cfg.workflow.daemon.task_schedule = req.daemon["task_schedule"]
                    if "eod_schedule" in req.daemon:
                        app_cfg.workflow.daemon.eod_schedule = req.daemon["eod_schedule"]
            if "approval_timeout_hours" in req.daemon:
                app_cfg.workflow.daemon.approval_timeout_hours = req.daemon["approval_timeout_hours"]
            if "approval_on_timeout" in req.daemon:
                app_cfg.workflow.daemon.approval_on_timeout = req.daemon["approval_on_timeout"]

        return _config_view()

    @app.get("/api/config/diff")
    async def config_diff() -> dict[str, Any]:
        """Compare in-memory config to the workflow.yaml on disk."""
        from devflow.config import load_workflow_config

        config_dir = getattr(app.state, "config_dir", "config")
        disk_path = Path(config_dir) / "workflow.yaml"
        if not disk_path.exists():
            return {"changed": [], "clean": False, "note": "workflow.yaml not found"}
        try:
            disk_cfg = load_workflow_config(disk_path)
        except Exception as exc:
            return {"changed": [], "clean": False, "note": str(exc)}

        current = _config_view()
        disk_view = {
            "task_source": disk_cfg.task_source,
            "hitl_strategy": disk_cfg.hitl_strategy,
            "todo_path": disk_cfg.todo_path,
            "human_in_the_loop": disk_cfg.human_in_the_loop,
            "daemon": {
                "task_schedule": disk_cfg.daemon.task_schedule,
                "eod_schedule": disk_cfg.daemon.eod_schedule,
                "approval_timeout_hours": disk_cfg.daemon.approval_timeout_hours,
                "approval_on_timeout": disk_cfg.daemon.approval_on_timeout,
            },
        }
        changed: list[dict[str, Any]] = []
        for field, disk_val in disk_view.items():
            if field == "daemon":
                for dk, dv in disk_val.items():
                    cv = current["daemon"][dk]
                    if cv != dv:
                        changed.append({"field": f"daemon.{dk}", "in_memory": cv, "on_disk": dv})
            elif current.get(field) != disk_val:
                changed.append({"field": field, "in_memory": current.get(field), "on_disk": disk_val})
        return {"changed": changed, "clean": len(changed) == 0}

    @app.post("/api/config/save")
    async def save_config() -> dict[str, Any]:
        """Persist the current in-memory config to workflow.yaml (atomic)."""
        config_dir = getattr(app.state, "config_dir", "config")
        wf_path = Path(config_dir) / "workflow.yaml"
        data = {
            "task_source": app_cfg.workflow.task_source,
            "max_rework_iterations": app_cfg.workflow.max_rework_iterations,
            "human_in_the_loop": app_cfg.workflow.human_in_the_loop,
            "default_branch": app_cfg.workflow.default_branch,
            "pr_target_branch": app_cfg.workflow.pr_target_branch,
            "corporate_report_channels": app_cfg.workflow.corporate_report_channels,
            "todo_path": app_cfg.workflow.todo_path,
            "hitl_strategy": app_cfg.workflow.hitl_strategy,
            "daemon": {
                "enabled": app_cfg.workflow.daemon.enabled,
                "task_schedule": app_cfg.workflow.daemon.task_schedule,
                "eod_schedule": app_cfg.workflow.daemon.eod_schedule,
                "port": app_cfg.workflow.daemon.port,
                "approval_timeout_hours": app_cfg.workflow.daemon.approval_timeout_hours,
                "approval_on_timeout": app_cfg.workflow.daemon.approval_on_timeout,
                "serve_frontend": app_cfg.workflow.daemon.serve_frontend,
                "frontend_dist": app_cfg.workflow.daemon.frontend_dist,
            },
            "forge": {
                "provider": app_cfg.workflow.forge.provider,
                "target_branch": app_cfg.workflow.forge.target_branch,
                "actions": app_cfg.workflow.forge.actions,
            },
        }
        content = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
        dir_ = wf_path.parent
        fd, tmp_name = tempfile.mkstemp(dir=str(dir_), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.replace(tmp_name, wf_path)
        except Exception as exc:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise HTTPException(status_code=500, detail=f"Failed to persist: {exc}") from exc
        return {"path": str(wf_path)}
```

Also, in `src/devflow/daemon/__main__.py`, after `app = create_app(...)`, add `app.state.config_dir = config_dir` so the save/diff endpoints know where `workflow.yaml` lives:

```python
    app = create_app(
        app_cfg, locks, event_bus, runner,
        approval_store=approval_store, eod_handler=eod_handler,
        scheduler=scheduler,
    )
    app.state.config_dir = config_dir  # for /api/config/save + /api/config/diff
    runner._on_task_change = app.state.set_current_task  # type: ignore[attr-defined]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_web_controls.py -k config -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/devflow/daemon/web.py src/devflow/daemon/__main__.py tests/unit/daemon/test_web_controls.py
git commit -m "feat(daemon): GET/PATCH/diff/save /api/config — runtime config management"
```

---

### Task 7: `PUT /api/config/hitl` endpoint (HITL switcher + EOD job)

**Files:**
- Modify: `src/devflow/daemon/web.py` (add endpoint)
- Test: `tests/unit/daemon/test_web_controls.py` (append hitl tests)

**Interfaces:**
- Consumes: `HitlStrategy.ALL`, `app.state.scheduler.set_eod_job`
- Produces: `PUT /api/config/hitl`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/daemon/test_web_controls.py`:

```python
def test_put_hitl_switches_strategy(tmp_path: Path) -> None:
    cfg = Config(
        workflow=WorkflowConfig(task_source="mock", hitl_strategy="per_plan"),
        providers={},
        agents={},
    )
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    sched = MagicMock()
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=sched)
    with TestClient(app) as client:
        resp = client.put("/api/config/hitl", json={"strategy": "end_of_day"})
    assert resp.status_code == 200
    assert cfg.workflow.hitl_strategy == "end_of_day"
    # Switching TO end_of_day enables the EOD job.
    sched.set_eod_job.assert_called_once_with(enabled=True, repo_path=".")


def test_put_hitl_switching_away_disables_eod(tmp_path: Path) -> None:
    cfg = Config(
        workflow=WorkflowConfig(task_source="mock", hitl_strategy="end_of_day"),
        providers={},
        agents={},
    )
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    sched = MagicMock()
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=sched)
    with TestClient(app) as client:
        resp = client.put("/api/config/hitl", json={"strategy": "per_plan"})
    assert resp.status_code == 200
    sched.set_eod_job.assert_called_once_with(enabled=False, repo_path=".")


def test_put_hitl_invalid_strategy_422(tmp_path: Path) -> None:
    cfg = Config(workflow=WorkflowConfig(task_source="mock"), providers={}, agents={})
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=MagicMock())
    with TestClient(app) as client:
        resp = client.put("/api/config/hitl", json={"strategy": "bogus"})
    assert resp.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_web_controls.py -k hitl -v`
Expected: FAIL — `404 Not Found`

- [ ] **Step 3: Write minimal implementation**

Add Pydantic model near the other models in `web.py`:
```python
from devflow.config import HitlStrategy  # add to imports at top


class HitlSwitchRequest(BaseModel):
    """Body of PUT /api/config/hitl."""

    strategy: str
```

Add endpoint inside `create_app` (after `save_config`, before `/api/events`):

```python
    @app.put("/api/config/hitl")
    async def switch_hitl(req: HitlSwitchRequest) -> dict[str, Any]:
        """Switch the HITL strategy at runtime + re-evaluate the EOD cron job."""
        if req.strategy not in HitlStrategy.ALL:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid strategy: {req.strategy}. Must be one of {sorted(HitlStrategy.ALL)}",
            )
        old = app_cfg.workflow.hitl_strategy
        app_cfg.workflow.hitl_strategy = req.strategy

        sched_obj = app.state.scheduler
        if sched_obj is not None:
            # Enable EOD job only when switching TO end_of_day.
            should_enable_eod = req.strategy == HitlStrategy.END_OF_DAY
            was_end_of_day = old == HitlStrategy.END_OF_DAY
            if should_enable_eod != was_end_of_day:
                sched_obj.set_eod_job(enabled=should_enable_eod, repo_path=".")
        logger.info("HITL strategy switched: %s -> %s", old, req.strategy)
        return {"strategy": req.strategy, "previous": old}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_web_controls.py -k hitl -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/devflow/daemon/web.py tests/unit/daemon/test_web_controls.py
git commit -m "feat(daemon): PUT /api/config/hitl — runtime HITL strategy switch"
```

---

### Task 8: `GET /api/agents` + `GET/PUT /api/agents/{name}` + save endpoints

**Files:**
- Modify: `src/devflow/daemon/web.py` (add endpoints)
- Test: `tests/unit/daemon/test_web_controls.py` (append agents tests)

**Interfaces:**
- Consumes: `app_cfg.agents` (`dict[str, AgentConfig]`), `frontmatter` library, `app.state.config_dir`
- Produces: `GET /api/agents`, `GET /api/agents/{name}`, `PUT /api/agents/{name}/prompt`, `POST /api/agents/{name}/save`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/daemon/test_web_controls.py`:

```python
def _make_app_with_agents(tmp_path: Path) -> tuple:
    from devflow.config import AgentConfig
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "planner.md").write_text(
        "---\nname: planner\nprovider: mock\nmodel: mock-model\ntemperature: 0.3\n---\n\nYou are the planner.\n",
        encoding="utf-8",
    )
    cfg = Config(
        workflow=WorkflowConfig(task_source="mock"),
        providers={},
        agents={
            "planner": AgentConfig(
                name="planner", provider="mock", model="mock-model",
                temperature=0.3, system_prompt="You are the planner.",
            ),
        },
    )
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=MagicMock())
    app.state.config_dir = str(tmp_path)
    return app, tmp_path


def test_get_agents_returns_list(tmp_path: Path) -> None:
    app, _ = _make_app_with_agents(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "planner"
    assert data[0]["provider"] == "mock"


def test_get_agent_detail(tmp_path: Path) -> None:
    app, _ = _make_app_with_agents(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/agents/planner")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "planner"
    assert data["system_prompt"] == "You are the planner."


def test_get_agent_unknown_404(tmp_path: Path) -> None:
    app, _ = _make_app_with_agents(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/agents/bogus")
    assert resp.status_code == 404


def test_put_agent_prompt_mutates_in_memory(tmp_path: Path) -> None:
    app, _ = _make_app_with_agents(tmp_path)
    with TestClient(app) as client:
        resp = client.put(
            "/api/agents/planner/prompt",
            json={"system_prompt": "You are a better planner."},
        )
    assert resp.status_code == 200
    # In-memory mutation takes effect immediately.
    assert app.state.cfg.agents["planner"].system_prompt == "You are a better planner."


def test_save_agent_writes_md_to_disk(tmp_path: Path) -> None:
    app, tmp = _make_app_with_agents(tmp_path)
    with TestClient(app) as client:
        client.put(
            "/api/agents/planner/prompt",
            json={"system_prompt": "You are a saved planner."},
        )
        resp = client.post("/api/agents/planner/save")
    assert resp.status_code == 200
    written = (tmp / "agents" / "planner.md").read_text(encoding="utf-8")
    assert "You are a saved planner." in written
    assert "name: planner" in written
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_web_controls.py -k agents -v`
Expected: FAIL — `404 Not Found`

- [ ] **Step 3: Write minimal implementation**

In `src/devflow/daemon/web.py`, add `import frontmatter` to imports (already a dependency via `config.py`).

Add Pydantic model:
```python
class AgentPromptUpdate(BaseModel):
    """Body of PUT /api/agents/{name}/prompt."""

    system_prompt: str
```

Add endpoints inside `create_app` (after hitl endpoint, before `/api/events`):

```python
    @app.get("/api/agents")
    async def list_agents() -> list[dict[str, Any]]:
        return [
            {
                "name": a.name,
                "provider": a.provider,
                "model": a.model,
                "temperature": a.temperature,
                "has_prompt": bool(a.system_prompt),
            }
            for a in app_cfg.agents.values()
        ]

    @app.get("/api/agents/{name}")
    async def get_agent(name: str) -> dict[str, Any]:
        a = app_cfg.agents.get(name)
        if a is None:
            raise HTTPException(status_code=404, detail=f"Unknown agent: {name}")
        return {
            "name": a.name,
            "provider": a.provider,
            "model": a.model,
            "temperature": a.temperature,
            "system_prompt": a.system_prompt,
            "skills": a.skills,
            "tools": a.tools,
            "auto_approve": a.auto_approve,
        }

    @app.put("/api/agents/{name}/prompt")
    async def update_agent_prompt(name: str, req: AgentPromptUpdate) -> dict[str, Any]:
        a = app_cfg.agents.get(name)
        if a is None:
            raise HTTPException(status_code=404, detail=f"Unknown agent: {name}")
        a.system_prompt = req.system_prompt  # in-memory, instant effect on next run
        logger.info("Agent prompt updated in-memory: %s", name)
        return {"name": name, "status": "updated"}

    @app.post("/api/agents/{name}/save")
    async def save_agent(name: str) -> dict[str, Any]:
        a = app_cfg.agents.get(name)
        if a is None:
            raise HTTPException(status_code=404, detail=f"Unknown agent: {name}")
        config_dir = getattr(app.state, "config_dir", "config")
        agents_dir = Path(config_dir) / "agents"
        agent_path = agents_dir / f"{name}.md"
        post = frontmatter.Post(a.system_prompt.lstrip("\n"))
        post.metadata = {
            "name": a.name,
            "provider": a.provider,
            "model": a.model,
            "temperature": a.temperature,
        }
        if a.auto_approve:
            post.metadata["auto_approve"] = True
        if a.skills:
            post.metadata["skills"] = a.skills
        if a.tools:
            post.metadata["tools"] = a.tools
        content = frontmatter.dumps(post)
        dir_ = agent_path.parent
        fd, tmp_name = tempfile.mkstemp(dir=str(dir_), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.replace(tmp_name, agent_path)
        except Exception as exc:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise HTTPException(status_code=500, detail=f"Failed to persist: {exc}") from exc
        return {"path": str(agent_path)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_web_controls.py -k agents -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run full backend control test suite**

Run: `python -m pytest tests/unit/daemon/test_web_controls.py -v`
Expected: PASS (all tests from Tasks 3-8)

- [ ] **Step 6: Run full daemon test suite to confirm no regression**

Run: `python -m pytest tests/unit/daemon/ tests/integration/test_dashboard_e2e.py -v`
Expected: PASS (all tests)

- [ ] **Step 7: Commit**

```bash
git add src/devflow/daemon/web.py tests/unit/daemon/test_web_controls.py
git commit -m "feat(daemon): GET/PUT/POST /api/agents — agent prompt management"
```

---

### Task 9: Frontend API client + types (control endpoints)

**Files:**
- Modify: `frontend/src/api/client.ts` (add `patchJson`, `putJson`, control functions)
- Modify: `frontend/src/api/types.ts` (add control types)

**Interfaces:**
- Consumes: existing `getJson`/`postJson` pattern
- Produces: typed client functions for all control endpoints

- [ ] **Step 1: Add types to `frontend/src/api/types.ts`**

Append to the file:

```typescript
// --- Control endpoints (P3) ---

export interface RunRequest {
  task_id?: string
  repo_path?: string
}

export interface RunResponse {
  run_id: string
  task_id: string | null
  status: string
}

export interface TodoItem {
  line_no: number
  text: string
  checkbox: string | null
  priority: number | null
  task_ref: string | null
  url: string | null
  title: string
}

export interface TodoPatch {
  priority?: number
  status?: 'open' | 'in_progress' | 'done'
}

export interface ConfigResponse {
  task_source: string
  hitl_strategy: string
  todo_path: string
  human_in_the_loop: boolean
  daemon: {
    enabled: boolean
    task_schedule: string
    eod_schedule: string
    port: number
    approval_timeout_hours: number
    approval_on_timeout: string
    serve_frontend: boolean
    frontend_dist: string
  }
  forge: {
    provider: string
    target_branch: string
    actions: string[]
  }
}

export interface ConfigPatch {
  hitl_strategy?: string
  daemon?: {
    task_schedule?: string
    eod_schedule?: string
    approval_timeout_hours?: number
    approval_on_timeout?: string
  }
}

export interface ConfigDiffEntry {
  field: string
  in_memory: unknown
  on_disk: unknown
}

export interface ConfigDiff {
  changed: ConfigDiffEntry[]
  clean: boolean
  note?: string
}

export type HitlStrategy = 'per_plan' | 'full_detail' | 'end_of_day'

export interface HitlSwitchResponse {
  strategy: string
  previous: string
}

export interface AgentSummary {
  name: string
  provider: string
  model: string
  temperature: number
  has_prompt: boolean
}

export interface AgentDetail {
  name: string
  provider: string
  model: string
  temperature: number
  system_prompt: string
  skills: string[]
  tools: string[]
  auto_approve: boolean
}

export interface AgentPromptUpdate {
  system_prompt: string
}
```

- [ ] **Step 2: Add HTTP helpers + control functions to `frontend/src/api/client.ts`**

Add `patchJson` and `putJson` helpers after `postJson`:

```typescript
async function patchJson<T>(path: string, body?: unknown): Promise<T> {
  const resp = await fetch(`${BASE}api${path}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!resp.ok) {
    throw new Error(`PATCH ${path} failed: ${resp.status} ${resp.statusText}`)
  }
  return resp.json() as Promise<T>
}

async function putJson<T>(path: string, body?: unknown): Promise<T> {
  const resp = await fetch(`${BASE}api${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!resp.ok) {
    throw new Error(`PUT ${path} failed: ${resp.status} ${resp.statusText}`)
  }
  return resp.json() as Promise<T>
}
```

Add the control endpoint functions at the end of the file:

```typescript
// --- Control: run tasks ---
export const runTask = (req: RunRequest) =>
  postJson<RunResponse>('/tasks/run', req)

// --- Control: TODO ---
export const getTodo = () => getJson<TodoItem[]>('/todo')
export const patchTodo = (lineNo: number, patch: TodoPatch) =>
  patchJson<TodoItem>(`/todo/${lineNo}`, patch)

// --- Control: config ---
export const getConfig = () => getJson<ConfigResponse>('/config')
export const patchConfig = (patch: ConfigPatch) =>
  patchJson<ConfigResponse>('/config', patch)
export const getConfigDiff = () => getJson<ConfigDiff>('/config/diff')
export const saveConfig = () => postJson<{ path: string }>('/config/save')
export const switchHitl = (strategy: HitlStrategy) =>
  putJson<HitlSwitchResponse>('/config/hitl', { strategy })

// --- Control: agents ---
export const getAgents = () => getJson<AgentSummary[]>('/agents')
export const getAgent = (name: string) =>
  getJson<AgentDetail>(`/agents/${encodeURIComponent(name)}`)
export const updateAgentPrompt = (name: string, prompt: string) =>
  putJson<{ name: string; status: string }>(
    `/agents/${encodeURIComponent(name)}/prompt`,
    { system_prompt: prompt },
  )
export const saveAgent = (name: string) =>
  postJson<{ path: string }>(`/agents/${encodeURIComponent(name)}/save`)
```

Update the import block at the top of `client.ts` to include the new types:

```typescript
import type {
  AgentDetail,
  AgentPromptUpdate,
  AgentSummary,
  ApprovalDecision,
  ApprovalPending,
  BatchEntryDetail,
  ConfigDiff,
  ConfigPatch,
  ConfigResponse,
  EodEntrySummary,
  EodPublishResult,
  HealthResponse,
  HitlStrategy,
  HitlSwitchResponse,
  RunRequest,
  RunResponse,
  StateResponse,
  TaskCurrentResponse,
  TaskQueueResponse,
  TodoItem,
  TodoPatch,
} from './types'
```

- [ ] **Step 3: Verify typecheck passes**

Run: `cd frontend && npm run typecheck`
Expected: PASS (no type errors)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/api/types.ts
git commit -m "feat(frontend): add control API client + types (run/todo/config/agents)"
```

---

### Task 10: Frontend stores (controls, config, todo, agents)

**Files:**
- Create: `frontend/src/stores/controls.ts`
- Create: `frontend/src/stores/config.ts`
- Create: `frontend/src/stores/todo.ts`
- Create: `frontend/src/stores/agents.ts`

**Interfaces:**
- Consumes: API client functions from Task 9
- Produces: 4 Pinia stores for the ControlsView tabs

- [ ] **Step 1: Create `frontend/src/stores/controls.ts`**

```typescript
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { runTask } from '@/api/client'
import type { RunResponse } from '@/api/types'

interface RunHistoryEntry {
  run_id: string
  task_id: string | null
  started_at: number
  status: 'started' | 'finished' | 'error'
}

export const useControlsStore = defineStore('controls', () => {
  const isRunning = ref(false)
  const currentRun = ref<RunResponse | null>(null)
  const runHistory = ref<RunHistoryEntry[]>([])
  const error = ref<string | null>(null)

  async function run(taskId?: string) {
    error.value = null
    try {
      const resp = await runTask({ task_id: taskId })
      currentRun.value = resp
      isRunning.value = true
      runHistory.value.unshift({
        run_id: resp.run_id,
        task_id: resp.task_id,
        started_at: Date.now(),
        status: 'started',
      })
      // Keep only last 5.
      if (runHistory.value.length > 5) {
        runHistory.value = runHistory.value.slice(0, 5)
      }
      return resp
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      throw e
    }
  }

  function markFinished(runId: string, status: 'finished' | 'error') {
    const entry = runHistory.value.find((r) => r.run_id === runId)
    if (entry) entry.status = status
    isRunning.value = false
    currentRun.value = null
  }

  function clearError() {
    error.value = null
  }

  return { isRunning, currentRun, runHistory, error, run, markFinished, clearError }
})
```

- [ ] **Step 2: Create `frontend/src/stores/config.ts`**

```typescript
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getConfig, getConfigDiff, patchConfig, saveConfig, switchHitl } from '@/api/client'
import type { ConfigDiff, ConfigPatch, ConfigResponse, HitlStrategy } from '@/api/types'

export const useConfigStore = defineStore('config-control', () => {
  const config = ref<ConfigResponse | null>(null)
  const diff = ref<ConfigDiff | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const saving = ref(false)

  async function fetch() {
    loading.value = true
    error.value = null
    try {
      const [cfg, d] = await Promise.all([getConfig(), getConfigDiff()])
      config.value = cfg
      diff.value = d
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  async function patch(patch: ConfigPatch) {
    error.value = null
    try {
      config.value = await patchConfig(patch)
      diff.value = await getConfigDiff()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      throw e
    }
  }

  async function setHitl(strategy: HitlStrategy) {
    error.value = null
    try {
      await switchHitl(strategy)
      if (config.value) config.value.hitl_strategy = strategy
      diff.value = await getConfigDiff()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      throw e
    }
  }

  async function save() {
    saving.value = true
    error.value = null
    try {
      await saveConfig()
      diff.value = await getConfigDiff()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      throw e
    } finally {
      saving.value = false
    }
  }

  return { config, diff, loading, error, saving, fetch, patch, setHitl, save }
})
```

- [ ] **Step 3: Create `frontend/src/stores/todo.ts`**

```typescript
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getTodo, patchTodo } from '@/api/client'
import type { TodoItem, TodoPatch } from '@/api/types'

export const useTodoStore = defineStore('todo-control', () => {
  const items = ref<TodoItem[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetch() {
    loading.value = true
    error.value = null
    try {
      items.value = await getTodo()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  async function updateLine(lineNo: number, patch: TodoPatch) {
    error.value = null
    try {
      const updated = await patchTodo(lineNo, patch)
      const idx = items.value.findIndex((it) => it.line_no === lineNo)
      if (idx >= 0) items.value[idx] = updated
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      throw e
    }
  }

  return { items, loading, error, fetch, updateLine }
})
```

- [ ] **Step 4: Create `frontend/src/stores/agents.ts`**

```typescript
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getAgent, getAgents, saveAgent, updateAgentPrompt } from '@/api/client'
import type { AgentDetail, AgentSummary } from '@/api/types'

export const useAgentsStore = defineStore('agents-control', () => {
  const agents = ref<AgentSummary[]>([])
  const current = ref<AgentDetail | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  // Track which agents have unsaved in-memory prompt changes.
  const modified = ref<Set<string>>(new Set())

  async function fetchList() {
    loading.value = true
    error.value = null
    try {
      agents.value = await getAgents()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  async function select(name: string) {
    error.value = null
    try {
      current.value = await getAgent(name)
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    }
  }

  async function updatePrompt(name: string, prompt: string) {
    error.value = null
    try {
      await updateAgentPrompt(name, prompt)
      if (current.value && current.value.name === name) {
        current.value.system_prompt = prompt
      }
      modified.value.add(name)
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      throw e
    }
  }

  async function save(name: string) {
    error.value = null
    try {
      await saveAgent(name)
      modified.value.delete(name)
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      throw e
    }
  }

  return { agents, current, loading, error, modified, fetchList, select, updatePrompt, save }
})
```

- [ ] **Step 5: Verify typecheck passes**

Run: `cd frontend && npm run typecheck`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/stores/controls.ts frontend/src/stores/config.ts frontend/src/stores/todo.ts frontend/src/stores/agents.ts
git commit -m "feat(frontend): add Pinia stores for controls (run/config/todo/agents)"
```

---

### Task 11: ControlsView + 4 tab components + routing + nav

**Files:**
- Create: `frontend/src/views/ControlsView.vue`
- Create: `frontend/src/components/controls/RunTab.vue`
- Create: `frontend/src/components/controls/TodoTab.vue`
- Create: `frontend/src/components/controls/ConfigTab.vue`
- Create: `frontend/src/components/controls/AgentsTab.vue`
- Modify: `frontend/src/router/index.ts` (add route)
- Modify: `frontend/src/App.vue` (add nav link)
- Modify: `frontend/src/composables/useSSE.ts` (refresh controlsStore)
- Modify: `frontend/src/style.css` (tabs, priority colors, badges)

- [ ] **Step 1: Create `frontend/src/components/controls/RunTab.vue`**

```vue
<script setup lang="ts">
import { ref } from 'vue'
import { useControlsStore } from '@/stores/controls'

const controls = useControlsStore()
const taskId = ref('')

async function onRun() {
  try {
    await controls.run(taskId.value || undefined)
  } catch {
    // error already in store
  }
}
</script>

<template>
  <section>
    <h3>Run a task</h3>
    <p v-if="controls.error" class="error">{{ controls.error }}</p>
    <div class="actions">
      <input
        v-model="taskId"
        placeholder="Task ID (empty = run next by priority)"
        :disabled="controls.isRunning"
      />
      <button @click="onRun" :disabled="controls.isRunning">
        {{ taskId ? 'Run task' : 'Run next' }}
      </button>
    </div>
    <p v-if="controls.isRunning" class="running-badge">
      ⏳ Running: <strong>{{ controls.currentRun?.task_id ?? 'next by priority' }}</strong>
    </p>
    <p v-else class="idle-badge">✓ Idle</p>

    <h4>Recent runs</h4>
    <ul v-if="controls.runHistory.length">
      <li v-for="r in controls.runHistory" :key="r.run_id">
        <code>{{ r.task_id ?? 'next' }}</code> —
        <span :class="r.status">{{ r.status }}</span>
        <small> ({{ new Date(r.started_at).toLocaleTimeString() }})</small>
      </li>
    </ul>
    <p v-else>No runs yet this session.</p>
  </section>
</template>
```

- [ ] **Step 2: Create `frontend/src/components/controls/TodoTab.vue`**

```vue
<script setup lang="ts">
import { onMounted } from 'vue'
import { useTodoStore } from '@/stores/todo'

const todo = useTodoStore()
onMounted(() => void todo.fetch())

function priorityClass(p: number | null): string {
  if (p === 0) return 'prio-critical'
  if (p === 1) return 'prio-urgent'
  return 'prio-normal'
}

async function changePriority(lineNo: number, priority: number) {
  try {
    await todo.updateLine(lineNo, { priority })
  } catch {
    // error in store
  }
}

async function toggleStatus(lineNo: number, current: string) {
  const next = current === '[ ]' ? 'in_progress' : current === '[~]' ? 'done' : 'open'
  try {
    await todo.updateLine(lineNo, { status: next })
  } catch {
    // error in store
  }
}
</script>

<template>
  <section>
    <h3>TODO priorities</h3>
    <p v-if="todo.error" class="error">{{ todo.error }}</p>
    <p v-if="todo.loading">Loading…</p>
    <table v-if="todo.items.length">
      <thead>
        <tr><th>Line</th><th>Status</th><th>Priority</th><th>Title</th></tr>
      </thead>
      <tbody>
        <tr v-for="it in todo.items.filter(i => i.checkbox !== null)" :key="it.line_no">
          <td><code>{{ it.line_no }}</code></td>
          <td>
            <button class="checkbox-btn" @click="toggleStatus(it.line_no, it.checkbox!)">
              {{ it.checkbox }}
            </button>
          </td>
          <td>
            <select
              :value="it.priority ?? ''"
              :class="priorityClass(it.priority)"
              @change="changePriority(it.line_no, Number(($event.target as HTMLSelectElement).value))"
            >
              <option v-for="n in 6" :key="n - 1" :value="n - 1">#r{{ n - 1 }}</option>
            </select>
          </td>
          <td>{{ it.title }}</td>
        </tr>
      </tbody>
    </table>
    <p v-else>No TODO entries found.</p>
  </section>
</template>
```

- [ ] **Step 3: Create `frontend/src/components/controls/ConfigTab.vue`**

```vue
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useConfigStore } from '@/stores/config'
import type { HitlStrategy } from '@/api/types'

const config = useConfigStore()
const showDiffWarning = ref(false)
onMounted(() => void config.fetch())

const strategies: HitlStrategy[] = ['per_plan', 'full_detail', 'end_of_day']

async function onHitl(strategy: HitlStrategy) {
  try {
    await config.setHitl(strategy)
  } catch {
    // error in store
  }
}

async function onPatch(field: string, value: unknown) {
  try {
    if (field.startsWith('daemon.')) {
      const sub = field.split('.')[1]
      await config.patch({ daemon: { [sub]: value } })
    } else {
      await config.patch({ [field]: value })
    }
  } catch {
    // error in store
  }
}

async function onSave() {
  showDiffWarning.value = true
}

async function confirmSave() {
  showDiffWarning.value = false
  try {
    await config.save()
  } catch {
    // error in store
  }
}
</script>

<template>
  <section>
    <h3>Workflow configuration</h3>
    <p v-if="config.error" class="error">{{ config.error }}</p>
    <p v-if="config.loading">Loading…</p>

    <div v-if="config.config" class="card">
      <h4>HITL strategy</h4>
      <div v-for="s in strategies" :key="s">
        <label>
          <input
            type="radio"
            :value="s"
            :checked="config.config.hitl_strategy === s"
            @change="onHitl(s)"
          />
          <code>{{ s }}</code>
        </label>
      </div>
    </div>

    <div v-if="config.config" class="card">
      <h4>Schedules</h4>
      <dl>
        <dt>Task schedule (cron)</dt>
        <dd>
          <input
            :value="config.config.daemon.task_schedule"
            @change="onPatch('daemon.task_schedule', ($event.target as HTMLInputElement).value)"
          />
        </dd>
        <dt>EOD schedule (cron)</dt>
        <dd>
          <input
            :value="config.config.daemon.eod_schedule"
            @change="onPatch('daemon.eod_schedule', ($event.target as HTMLInputElement).value)"
          />
        </dd>
      </dl>
    </div>

    <div v-if="config.config" class="card">
      <h4>Approval</h4>
      <dl>
        <dt>Timeout (hours)</dt>
        <dd>
          <input
            type="number"
            :value="config.config.daemon.approval_timeout_hours"
            @change="onPatch('daemon.approval_timeout_hours', Number(($event.target as HTMLInputElement).value))"
          />
        </dd>
        <dt>On timeout</dt>
        <dd>
          <select
            :value="config.config.daemon.approval_on_timeout"
            @change="onPatch('daemon.approval_on_timeout', ($event.target as HTMLSelectElement).value)"
          >
            <option value="defer">defer</option>
            <option value="reject">reject</option>
          </select>
        </dd>
      </dl>
    </div>

    <div v-if="config.config" class="card">
      <h4>Restart-only fields (read-only)</h4>
      <dl>
        <dt>Port</dt><dd><code>{{ config.config.daemon.port }}</code> (restart only)</dd>
        <dt>Serve frontend</dt><dd><code>{{ config.config.daemon.serve_frontend }}</code> (restart only)</dd>
      </dl>
    </div>

    <div class="actions" v-if="config.diff && !config.diff.clean">
      <span class="unsaved-badge">⚠ Unsaved changes ({{ config.diff.changed.length }})</span>
      <button @click="onSave" :disabled="config.saving">Save to disk</button>
    </div>
    <div v-if="showDiffWarning" class="card">
      <p><strong>Warning:</strong> Saving overwrites <code>workflow.yaml</code> — comments will be lost.</p>
      <ul>
        <li v-for="c in config.diff?.changed" :key="c.field">
          <code>{{ c.field }}</code>: <s>{{ String(c.on_disk) }}</s> → <strong>{{ String(c.in_memory) }}</strong>
        </li>
      </ul>
      <button @click="confirmSave" :disabled="config.saving">Confirm save</button>
      <button @click="showDiffWarning = false">Cancel</button>
    </div>
  </section>
</template>
```

- [ ] **Step 4: Create `frontend/src/components/controls/AgentsTab.vue`**

```vue
<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { useAgentsStore } from '@/stores/agents'

const agents = useAgentsStore()
let saveTimer: ReturnType<typeof setTimeout> | null = null

onMounted(() => void agents.fetchList())

watch(
  () => agents.current?.name,
  (name) => {
    if (name) void agents.select(name)
  },
)

function onPromptInput(name: string, text: string) {
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => {
    void agents.updatePrompt(name, text)
  }, 1000)
}
</script>

<template>
  <section>
    <h3>Agent prompts</h3>
    <p v-if="agents.error" class="error">{{ agents.error }}</p>
    <div class="agents-layout">
      <ul class="agent-list">
        <li
          v-for="a in agents.agents"
          :key="a.name"
          :class="{ active: agents.current?.name === a.name, modified: agents.modified.has(a.name) }"
        >
          <button @click="agents.select(a.name)">
            {{ a.name }}
            <span v-if="agents.modified.has(a.name)" class="modified-dot">●</span>
          </button>
          <small>{{ a.provider }} / {{ a.model }}</small>
        </li>
      </ul>
      <div v-if="agents.current" class="agent-editor">
        <dl>
          <dt>Provider</dt><dd>{{ agents.current.provider }}</dd>
          <dt>Model</dt><dd>{{ agents.current.model }}</dd>
          <dt>Temperature</dt><dd>{{ agents.current.temperature }}</dd>
        </dl>
        <h4>System prompt</h4>
        <textarea
          :value="agents.current.system_prompt"
          @input="onPromptInput(agents.current!.name, ($event.target as HTMLTextAreaElement).value)"
          rows="16"
          class="prompt-textarea"
        ></textarea>
        <div class="actions">
          <span v-if="agents.modified.has(agents.current.name)" class="unsaved-badge">● Modified (in-memory)</span>
          <button @click="agents.save(agents.current.name)">Save to disk</button>
        </div>
      </div>
    </div>
  </section>
</template>
```

- [ ] **Step 5: Create `frontend/src/views/ControlsView.vue`**

```vue
<script setup lang="ts">
import { ref } from 'vue'
import RunTab from '@/components/controls/RunTab.vue'
import TodoTab from '@/components/controls/TodoTab.vue'
import ConfigTab from '@/components/controls/ConfigTab.vue'
import AgentsTab from '@/components/controls/AgentsTab.vue'

const activeTab = ref<'run' | 'todo' | 'config' | 'agents'>('run')
const tabs = [
  { id: 'run', label: 'Run' },
  { id: 'todo', label: 'TODO' },
  { id: 'config', label: 'Config' },
  { id: 'agents', label: 'Agents' },
] as const
</script>

<template>
  <section>
    <h2>Controls</h2>
    <div class="tabs">
      <button
        v-for="t in tabs"
        :key="t.id"
        :class="{ active: activeTab === t.id }"
        @click="activeTab = t.id"
      >
        {{ t.label }}
      </button>
    </div>
    <RunTab v-if="activeTab === 'run'" />
    <TodoTab v-else-if="activeTab === 'todo'" />
    <ConfigTab v-else-if="activeTab === 'config'" />
    <AgentsTab v-else-if="activeTab === 'agents'" />
  </section>
</template>
```

- [ ] **Step 6: Add route to `frontend/src/router/index.ts`**

Add to the `routes` array (before the not-found catch-all):

```typescript
  { path: '/controls', name: 'controls', component: () => import('@/views/ControlsView.vue') },
```

- [ ] **Step 7: Add nav link to `frontend/src/App.vue`**

Change the `<nav>` block to add a Controls link after EOD Review:

```vue
      <nav>
        <RouterLink to="/">Dashboard</RouterLink>
        <span> · </span>
        <RouterLink to="/approvals">Approvals</RouterLink>
        <span> · </span>
        <RouterLink to="/eod">EOD Review</RouterLink>
        <span> · </span>
        <RouterLink to="/controls">Controls</RouterLink>
      </nav>
```

- [ ] **Step 8: Extend `useSSE.ts` to refresh controlsStore**

In `frontend/src/composables/useSSE.ts`, add import and event handlers:

```typescript
import { useControlsStore } from '@/stores/controls'
```

Inside `connect()`, after the existing event listeners, update the `task.finished` and `task.error` handlers to also update the controls store:

```typescript
    const controls = useControlsStore()

    source.addEventListener('task.started', () => {
      void tasks.fetchCurrent()
    })
    source.addEventListener('task.finished', (ev) => {
      void tasks.fetchCurrent()
      void tasks.fetchDone()
      try {
        const data = JSON.parse((ev as MessageEvent).data)
        controls.markFinished(data.run_id ?? '', 'finished')
      } catch {
        controls.markFinished('', 'finished')
      }
    })
    source.addEventListener('task.error', (ev) => {
      void tasks.fetchCurrent()
      void daemon.fetchAll()
      try {
        const data = JSON.parse((ev as MessageEvent).data)
        controls.markFinished(data.run_id ?? '', 'error')
      } catch {
        controls.markFinished('', 'error')
      }
    })
```

**Note:** Replace the existing `task.finished` and `task.error` listeners — don't duplicate them. The new versions include the `controls.markFinished` calls.

- [ ] **Step 9: Add styles to `frontend/src/style.css`**

Append to `frontend/src/style.css`:

```css
/* --- Controls: tabs --- */
.tabs {
  display: flex;
  gap: 0.5rem;
  border-bottom: 1px solid #ddd;
  margin-bottom: 1rem;
}
.tabs button {
  border: none;
  border-bottom: 2px solid transparent;
  background: none;
  border-radius: 0;
  padding: 0.5rem 1rem;
}
.tabs button.active {
  border-bottom-color: #0366d6;
  color: #0366d6;
  font-weight: 600;
}
/* --- Controls: badges --- */
.running-badge { color: #0366d6; }
.idle-badge { color: #28a745; }
.unsaved-badge { color: #d97706; font-weight: 600; }
.modified-dot { color: #d97706; }
/* --- Controls: TODO priorities --- */
.prio-critical { color: #cb2431; font-weight: 600; }
.prio-urgent { color: #d97706; }
.prio-normal { color: #666; }
.checkbox-btn { border: 1px solid #ccc; padding: 0.1rem 0.4rem; }
/* --- Controls: agents layout --- */
.agents-layout { display: grid; grid-template-columns: 200px 1fr; gap: 1rem; }
.agent-list { list-style: none; padding: 0; margin: 0; }
.agent-list li { padding: 0.3rem 0; }
.agent-list li.active button { font-weight: 600; color: #0366d6; }
.agent-list li.modified button::after { content: " ●"; color: #d97706; }
.agent-editor textarea.prompt-textarea {
  width: 100%;
  font-family: "SFMono-Regular", Consolas, monospace;
  font-size: 0.85em;
  padding: 0.75rem;
  border: 1px solid #ddd;
  border-radius: 4px;
  resize: vertical;
}
/* --- status colors --- */
.finished { color: #28a745; }
.error { color: #cb2431; }
.started { color: #0366d6; }
```

- [ ] **Step 10: Verify typecheck + build**

Run: `cd frontend && npm run typecheck`
Expected: PASS

Run: `cd frontend && npm run build`
Expected: PASS (dist/ created)

- [ ] **Step 11: Commit**

```bash
git add frontend/src/views/ControlsView.vue frontend/src/components/ frontend/src/router/index.ts frontend/src/App.vue frontend/src/composables/useSSE.ts frontend/src/style.css
git commit -m "feat(frontend): ControlsView with Run/TODO/Config/Agents tabs + nav + SSE"
```

---

### Task 12: Manual smoke test via Playwright + final verification

**Files:**
- None (verification only)

- [ ] **Step 1: Rebuild frontend and restart daemon**

```bash
cd frontend && npm run build && cd ..
# Restart daemon (kill existing + restart)
```

- [ ] **Step 2: Verify backend endpoints respond**

```bash
curl -s http://127.0.0.1:8787/api/todo | head -c 200
curl -s http://127.0.0.1:8787/api/config | head -c 200
curl -s http://127.0.0.1:8787/api/agents | head -c 200
```
Expected: JSON responses, not 404.

- [ ] **Step 3: Open dashboard in Playwright and verify Controls tab**

Navigate to `http://127.0.0.1:8787/controls`. Verify:
- 4 tabs render (Run / TODO / Config / Agents)
- TODO tab shows entries
- Config tab shows current config
- Agents tab shows agent list

- [ ] **Step 4: Run full test suite**

```bash
python -m pytest tests/ -v
cd frontend && npm run typecheck && npm run build
```
Expected: All tests pass, typecheck clean, build succeeds.

- [ ] **Step 5: Final commit (if any verification artifacts need staging)**

```bash
git status
# Only commit if there are uncommitted changes from verification fixes
```

---

## Self-Review

**1. Spec coverage:**
- ✅ Запуск задач → Task 4 (`POST /api/tasks/run`)
- ✅ TODO приоритеты → Tasks 2, 5 (`GET/PATCH /api/todo`)
- ✅ Config management → Task 6 (`GET/PATCH/diff/save /api/config`)
- ✅ HITL switch → Task 7 (`PUT /api/config/hitl`)
- ✅ Agent prompts → Task 8 (`GET/PUT/POST /api/agents`)
- ✅ threading.Lock wiring → Task 3
- ✅ scheduler.reschedule + set_eod_job → Task 1
- ✅ Frontend ControlsView + 4 tabs → Task 11
- ✅ 4 Pinia stores → Task 10
- ✅ API client + types → Task 9
- ✅ SSE integration → Task 11 Step 8
- ✅ Smoke test → Task 12

**2. Placeholder scan:** No TBD/TODO. All steps contain actual code. ✅

**3. Type consistency:**
- `rewrite_todo_line(path, line_no, priority, status)` — consistent across Tasks 2, 5 ✅
- `reschedule(task_schedule, eod_schedule)` — consistent across Tasks 1, 6 ✅
- `set_eod_job(enabled, repo_path)` — consistent across Tasks 1, 7 ✅
- `RunTaskRequest{task_id, repo_path}` / `RunTaskResponse{run_id, task_id, status}` — consistent across Tasks 4, 9 ✅
- `TodoPatch{priority, status}` — consistent across Tasks 9, 10 ✅
- Frontend store method names (`run`, `fetch`, `patch`, `setHitl`, `save`, `updateLine`, `updatePrompt`) — consistent across Tasks 10, 11 ✅
