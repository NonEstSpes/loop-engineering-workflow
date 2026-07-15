# Dashboard Enhancements A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Redmine priority mapping, rename TODO.md → TASKS.md, list refresh (button+SSE+polling), graphical cron builder, and provider/model editing for agents.

**Architecture:** Backend: fix `priority_from_task` to return `None` for unknown priorities + rename config default. Frontend: new `CronBuilder.vue` component, rename TodoTab→TasksTab with refresh, extend AgentsTab with provider/model/temperature fields. New `GET /api/providers` endpoint + extended `PUT /api/agents/{name}`.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, pytest (backend); Vue 3 Composition API, TypeScript, Pinia, Vite (frontend).

## Global Constraints

- **Redmine priority parsing already works** (`redmine.py:207`) — do NOT modify it.
- **Priority mapping** (confirmed): Немедленный→r0, Срочный→r1, Нормальный→r3, Низкий→r4, unknown/empty→`None` (no `#rX` tag).
- **`_PRIORITY_MAP`** in `todo.py` already has correct mappings — only remove the default `5`.
- **Rename**: `TODO.md` → `TASKS.md` with fallback copy (don't delete old file).
- **`DEVFLOW_TODO_PATH`** env override stays working (it overrides the default).
- **Cron builder** is pure frontend — backend validation (`CronTrigger.from_crontab`) unchanged.
- **Agent field editing**: provider = dropdown from `/api/providers`; model = free text field.
- **Backend tests**: pytest in `tests/unit/`, follow existing patterns.
- **Frontend**: no test runner this round; verify via `npm run typecheck` + `npm run build`.
- **Commit after each task** — conventional commits.
- **Branch**: `feature/phase5-vue-dashboard` (working branch).

---

## File Structure

**Backend — modified:**
- `src/devflow/todo.py` — `priority_from_task` → `None`, `generate_todo_from_source` skips tag for `None`
- `src/devflow/config.py` — `todo_path` default → `TASKS.md`, fallback copy logic
- `src/devflow/daemon/web.py` — `GET /api/providers`, extend `PUT /api/agents/{name}`, SSE `tasks.updated` publish hook
- `src/devflow/cli.py` — `_write_todo_file` header text + help string → TASKS.md
- `README.md` — references TODO.md → TASKS.md
- `.gitignore` — `/TODO.md` → `/TASKS.md`

**Backend — tests modified:**
- `tests/unit/test_todo.py` — `None` priority tests
- `tests/unit/test_config.py` — `TASKS.md` default + fallback
- `tests/unit/daemon/test_web_controls.py` — providers, agent fields, tasks.updated SSE

**Frontend — new:**
- `frontend/src/components/controls/CronBuilder.vue`
- `frontend/src/components/controls/TasksTab.vue` (rename from TodoTab + refresh)

**Frontend — modified:**
- `frontend/src/components/controls/ConfigTab.vue` — use CronBuilder
- `frontend/src/components/controls/AgentsTab.vue` — provider/model/temperature editing
- `frontend/src/composables/useSSE.ts` — `tasks.updated` listener
- `frontend/src/api/client.ts` — `getProviders`, `updateAgent`
- `frontend/src/api/types.ts` — `ProviderSummary`, `AgentUpdate`
- `frontend/src/stores/agents.ts` — `providers` list
- `frontend/src/stores/todo.ts` — `lastUpdated` timestamp
- `frontend/src/views/ControlsView.vue` — tab label TODO → TASKS
- `frontend/src/style.css` — cron builder + refresh button styles

---

### Task 1: Fix `priority_from_task` to return `None` for unknown priorities

**Files:**
- Modify: `src/devflow/todo.py:319-322` (`priority_from_task`) and `:325-357` (`generate_todo_from_source`)
- Test: `tests/unit/test_todo.py` (extend)

**Interfaces:**
- Consumes: `_PRIORITY_MAP` (existing), `Task.metadata["priority"]`
- Produces: `priority_from_task(task: Task) -> int | None`; `generate_todo_from_source` produces lines without `#rX` when priority is `None`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_todo.py`:

```python
from devflow.state import Task


def _make_task(task_id: str = "1", priority: str | None = None) -> Task:
    metadata = {}
    if priority is not None:
        metadata["priority"] = priority
    return Task(id=task_id, title="Test task", description="", status="open", metadata=metadata)


def test_priority_from_task_maps_known_priorities() -> None:
    from devflow.todo import priority_from_task
    assert priority_from_task(_make_task(priority="Немедленный")) == 0
    assert priority_from_task(_make_task(priority="Срочный")) == 1
    assert priority_from_task(_make_task(priority="Нормальный")) == 3
    assert priority_from_task(_make_task(priority="Низкий")) == 4


def test_priority_from_task_returns_none_for_unknown() -> None:
    from devflow.todo import priority_from_task
    assert priority_from_task(_make_task(priority="")) is None
    assert priority_from_task(_make_task(priority="BogusName")) is None
    assert priority_from_task(_make_task()) is None  # no metadata key


def test_generate_todo_skips_tag_for_none_priority() -> None:
    from devflow.todo import generate_todo_from_source
    tasks = [
        _make_task(task_id="1", priority="Срочный"),
        _make_task(task_id="2"),  # no priority
    ]
    items = generate_todo_from_source(tasks)
    assert len(items) == 2
    # Task with priority gets #r1 tag.
    assert "#r1" in items[0].raw_line
    # Task without priority has NO #rX tag.
    assert "#r" not in items[1].raw_line
    assert items[1].priority is None


def test_generate_todo_sorts_none_priority_last() -> None:
    from devflow.todo import generate_todo_from_source
    tasks = [
        _make_task(task_id="3"),  # None priority
        _make_task(task_id="1", priority="Немедленный"),  # r0
        _make_task(task_id="2", priority="Низкий"),  # r4
    ]
    items = generate_todo_from_source(tasks)
    # r0 first, r4 second, None last.
    assert items[0].priority == 0
    assert items[1].priority == 4
    assert items[2].priority is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_todo.py::test_priority_from_task_returns_none_for_unknown -v`
Expected: FAIL — `assert 5 == None` (current default returns 5)

- [ ] **Step 3: Modify `priority_from_task` to return `int | None`**

In `src/devflow/todo.py:319`, change:

```python
def priority_from_task(task: Task) -> int:
    """Map a task's Redmine priority (in ``metadata``) to an r-level (0..5)."""
    raw = str(task.metadata.get("priority") or "").strip().lower()
    return _PRIORITY_MAP.get(raw, 5)
```

to:

```python
def priority_from_task(task: Task) -> int | None:
    """Map a task's Redmine priority (in ``metadata``) to an r-level (0..5).

    Returns ``None`` when the task has no priority or an unrecognized one,
    so :func:`generate_todo_from_source` can emit a line without a ``#rX`` tag.
    """
    raw = str(task.metadata.get("priority") or "").strip().lower()
    return _PRIORITY_MAP.get(raw)
```

- [ ] **Step 4: Modify `generate_todo_from_source` to handle `None` priority**

In `src/devflow/todo.py:325-357`, change the loop body. Replace:

```python
    sortable = sorted(
        tasks,
        key=lambda t: (priority_from_task(t), str(t.id)),
    )
    items: list[TodoItem] = []
    for task in sortable:
        r = priority_from_task(task)
        url = task.metadata.get("redmine_url")
        task_ref = str(task.id)
        if url:
            body = f"- {CHECKBOX_OPEN} #r{r} [#{task_ref}]({url}) — {task.title}"
        else:
            body = f"- {CHECKBOX_OPEN} #r{r} #{task_ref} — {task.title}"
        items.append(
            TodoItem(
                raw_line=body,
                line_no=0,  # assigned during render
                checkbox=CHECKBOX_OPEN,
                priority=r,
                task_ref=task_ref,
                url=url,
                title=task.title,
                result=None,
            )
        )
    return items
```

with:

```python
    sortable = sorted(
        tasks,
        # None-priority tasks sort last (99 sentinel), then by id.
        key=lambda t: (priority_from_task(t) if priority_from_task(t) is not None else 99, str(t.id)),
    )
    items: list[TodoItem] = []
    for task in sortable:
        r = priority_from_task(task)
        url = task.metadata.get("redmine_url")
        task_ref = str(task.id)
        # Only add a #rX tag when a priority is known.
        tag = f"#r{r} " if r is not None else ""
        if url:
            body = f"- {CHECKBOX_OPEN} {tag}[#{task_ref}]({url}) — {task.title}"
        else:
            body = f"- {CHECKBOX_OPEN} {tag}#{task_ref} — {task.title}"
        items.append(
            TodoItem(
                raw_line=body,
                line_no=0,  # assigned during render
                checkbox=CHECKBOX_OPEN,
                priority=r,
                task_ref=task_ref,
                url=url,
                title=task.title,
                result=None,
            )
        )
    return items
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_todo.py -v`
Expected: PASS (all priority tests)

- [ ] **Step 6: Run full test suite to check for regressions**

Run: `python -m pytest tests/ -q`
Expected: PASS (note: some existing tests may assume default `5` — fix them by updating assertions to `None`)

- [ ] **Step 7: Commit**

```bash
git add src/devflow/todo.py tests/unit/test_todo.py
git commit -m "fix(todo): priority_from_task returns None for unknown; skip #rX tag when None"
```

---

### Task 2: Rename `TODO.md` → `TASKS.md` (config default + fallback copy)

**Files:**
- Modify: `src/devflow/config.py:59` (default) + `:207-235` (load + fallback)
- Modify: `.gitignore:23`
- Test: `tests/unit/test_config.py` (extend)

**Interfaces:**
- Consumes: `WorkflowConfig.todo_path` field, `load_workflow_config`
- Produces: default `"TASKS.md"`, fallback copy `TODO.md` → `TASKS.md` on first load

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_config.py`:

```python
def test_workflow_config_default_todo_path_is_tasks_md() -> None:
    """The default todo_path is now TASKS.md (renamed from TODO.md)."""
    from devflow.config import WorkflowConfig
    cfg = WorkflowConfig(task_source="mock")
    assert cfg.todo_path == "TASKS.md"


def test_migrate_todo_to_tasks_copies_when_tasks_missing(tmp_path) -> None:
    """If TASKS.md is missing but TODO.md exists, copy TODO.md → TASKS.md."""
    from devflow.config import migrate_todo_to_tasks
    # Create a fake TODO.md.
    (tmp_path / "TODO.md").write_text("# Old TODO\n- [ ] task\n", encoding="utf-8")
    migrate_todo_to_tasks(tmp_path)
    # TASKS.md should now exist with the old content.
    assert (tmp_path / "TASKS.md").exists()
    assert "# Old TODO" in (tmp_path / "TASKS.md").read_text(encoding="utf-8")
    # TODO.md is NOT deleted (user keeps it as backup).
    assert (tmp_path / "TODO.md").exists()


def test_migrate_todo_to_tasks_noop_when_tasks_exists(tmp_path) -> None:
    """If TASKS.md already exists, do nothing (don't overwrite)."""
    from devflow.config import migrate_todo_to_tasks
    (tmp_path / "TASKS.md").write_text("# Existing TASKS\n", encoding="utf-8")
    (tmp_path / "TODO.md").write_text("# Old TODO\n", encoding="utf-8")
    migrate_todo_to_tasks(tmp_path)
    # TASKS.md unchanged.
    assert "# Existing TASKS" in (tmp_path / "TASKS.md").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_config.py::test_workflow_config_default_todo_path_is_tasks_md -v`
Expected: FAIL — default is still `"TODO.md"`

- [ ] **Step 3: Change default + add `migrate_todo_to_tasks` function**

In `src/devflow/config.py:59`, change:

```python
    todo_path: str = "TODO.md"
```

to:

```python
    todo_path: str = "TASKS.md"
```

Add a new function in `config.py` (after `load_workflow_config`, ~line 235):

```python
def migrate_todo_to_tasks(config_dir: Path) -> None:
    """One-time migration: if TASKS.md is missing but TODO.md exists, copy it.

    The old TODO.md is NOT deleted (kept as a backup). Called once during
    daemon/CLI startup so existing users keep their task list.
    """
    tasks_path = config_dir.parent / "TASKS.md" if config_dir.name == "config" else config_dir / "TASKS.md"
    todo_path = config_dir.parent / "TODO.md" if config_dir.name == "config" else config_dir / "TODO.md"
    # Resolve relative to CWD when the paths are not absolute.
    if not tasks_path.is_absolute():
        tasks_path = Path.cwd() / "TASKS.md"
        todo_path = Path.cwd() / "TODO.md"
    if not tasks_path.exists() and todo_path.exists():
        tasks_path.write_text(todo_path.read_text(encoding="utf-8"), encoding="utf-8")
        import logging
        logging.getLogger(__name__).info(
            "Migrated TODO.md → TASKS.md (old file kept as backup)."
        )
```

- [ ] **Step 4: Update `.gitignore`**

In `.gitignore:23`, change:

```
/TODO.md
```

to:

```
/TASKS.md
```

- [ ] **Step 5: Call migration in daemon startup**

In `src/devflow/daemon/__main__.py`, after `app_cfg = load_config(config_dir)` (line 43), add:

```python
    from devflow.config import migrate_todo_to_tasks
    migrate_todo_to_tasks(Path(config_dir))
```

And in `src/devflow/cli.py`, in the `run` command (after line 145 where `app_cfg.workflow.todo_path` is resolved), add the same migration call:

```python
    from pathlib import Path as _Path
    from devflow.config import migrate_todo_to_tasks
    migrate_todo_to_tasks(_Path("config"))
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_config.py -v`
Expected: PASS

- [ ] **Step 7: Update docstrings referencing TODO.md**

In `src/devflow/config.py:56-59`, update the comment:

```python
    # Path to the TASKS.md file that the orchestrator reads task entries from
    # and the reporter writes completion results back to. May be overridden by
    # the --todo-path CLI flag or the DEVFLOW_TODO_PATH env variable.
    todo_path: str = "TASKS.md"
```

- [ ] **Step 8: Commit**

```bash
git add src/devflow/config.py src/devflow/daemon/__main__.py src/devflow/cli.py .gitignore tests/unit/test_config.py
git commit -m "feat(config): rename TODO.md → TASKS.md with one-time fallback migration"
```

---

### Task 3: Backend — `GET /api/providers` + extend `PUT /api/agents/{name}`

**Files:**
- Modify: `src/devflow/daemon/web.py` (new endpoint + extend agent update)
- Test: `tests/unit/daemon/test_web_controls.py` (extend)

**Interfaces:**
- Consumes: `app_cfg.providers` (dict[str, ProviderConfig]), `app_cfg.agents`
- Produces: `GET /api/providers` → list of `{name, type}`; `PUT /api/agents/{name}` accepts `{system_prompt?, provider?, model?, temperature?}`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/daemon/test_web_controls.py`:

```python
def test_get_providers_returns_list() -> None:
    from devflow.config import ProviderConfig
    cfg = Config(
        workflow=WorkflowConfig(task_source="mock"),
        providers={
            "openai": ProviderConfig(name="openai", type="openai_compatible"),
            "kimi": ProviderConfig(name="kimi", type="openai_compatible"),
        },
        agents={"planner": AgentConfig(name="planner", provider="mock", model="m", system_prompt="x")},
    )
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=MagicMock())
    with TestClient(app) as client:
        resp = client.get("/api/providers")
    assert resp.status_code == 200
    data = resp.json()
    names = [p["name"] for p in data]
    assert "openai" in names
    assert "kimi" in names


def test_put_agent_updates_provider_and_model() -> None:
    app, _ = _make_app_with_agents(Path("."))  # uses existing helper
    # _make_app_with_agents creates cfg with planner agent; but it uses tmp_path.
    # Re-create with a real agents dict:
    from devflow.config import AgentConfig
    cfg = Config(
        workflow=WorkflowConfig(task_source="mock"),
        providers={"mock": __import__("devflow.config", fromlist=["ProviderConfig"]).ProviderConfig(name="mock")},
        agents={"planner": AgentConfig(name="planner", provider="openai", model="old-model", system_prompt="x", temperature=0.3)},
    )
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=MagicMock(), scheduler=MagicMock())
    with TestClient(app) as client:
        resp = client.put("/api/agents/planner", json={"provider": "kimi", "model": "GLM-5.2", "temperature": 0.5})
    assert resp.status_code == 200
    assert app.state.cfg.agents["planner"].provider == "kimi"
    assert app.state.cfg.agents["planner"].model == "GLM-5.2"
    assert app.state.cfg.agents["planner"].temperature == 0.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_web_controls.py::test_get_providers_returns_list -v`
Expected: FAIL — 404 (endpoint doesn't exist)

- [ ] **Step 3: Add `GET /api/providers` endpoint**

In `src/devflow/daemon/web.py`, add inside `create_app` (after `list_agents`, before `get_agent`):

```python
    @app.get("/api/providers")
    async def list_providers() -> list[dict[str, Any]]:
        return [
            {"name": p.name, "type": p.type}
            for p in app_cfg.providers.values()
        ]
```

- [ ] **Step 4: Add extended `PUT /api/agents/{name}` endpoint**

In `src/devflow/daemon/web.py`, add a new Pydantic model near `AgentPromptUpdate`:

```python
class AgentUpdate(BaseModel):
    """Body of PUT /api/agents/{name} — full or partial agent field update."""

    system_prompt: str | None = None
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
```

Add the endpoint inside `create_app` (after `update_agent_prompt`, before `save_agent`):

```python
    @app.put("/api/agents/{name}")
    async def update_agent(name: str, req: AgentUpdate) -> dict[str, Any]:
        """Update agent fields in-memory (prompt, provider, model, temperature)."""
        a = app_cfg.agents.get(name)
        if a is None:
            raise HTTPException(status_code=404, detail=f"Unknown agent: {name}")
        if req.system_prompt is not None:
            a.system_prompt = req.system_prompt
        if req.provider is not None:
            a.provider = req.provider
        if req.model is not None:
            a.model = req.model
        if req.temperature is not None:
            a.temperature = req.temperature
        logger.info("Agent updated in-memory: %s", name)
        return {"name": name, "status": "updated"}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_web_controls.py -k "providers or put_agent_updates" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/devflow/daemon/web.py tests/unit/daemon/test_web_controls.py
git commit -m "feat(daemon): GET /api/providers + PUT /api/agents/{name} (provider/model/temperature)"
```

---

### Task 4: Backend — publish `tasks.updated` SSE event after TASKS regeneration

**Files:**
- Modify: `src/devflow/cli.py:366-377` (`_write_todo_file`)
- Test: `tests/unit/daemon/test_web_controls.py` (extend — or test via EventBus)

**Interfaces:**
- Consumes: `EventBus.publish` (existing), `_write_todo_file`
- Produces: SSE event `tasks.updated` with `{path, count}` after TASKS.md is written

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/daemon/test_web_controls.py`:

```python
def test_tasks_updated_sse_event_published_after_write() -> None:
    """When _write_todo_file runs with an event_bus, it publishes tasks.updated."""
    import asyncio
    from devflow.config import Config, DaemonConfig, WorkflowConfig
    from devflow.daemon.events import EventBus, GLOBAL_TOPIC
    from devflow.daemon.locks import DaemonLocks
    from devflow.cli import _write_todo_file
    from devflow.state import Task

    cfg = Config(workflow=WorkflowConfig(task_source="mock", todo_path="TASKS.md"), providers={}, agents={})
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    tasks = [Task(id="1", title="Test", description="", status="open", metadata={})]

    async def _check() -> dict | None:
        queue = await bus.subscribe(GLOBAL_TOPIC)
        _write_todo_file(cfg, tasks, quiet=True, event_bus=bus)
        try:
            return await asyncio.wait_for(queue.get(), timeout=2.0)
        except TimeoutError:
            return None

    result = asyncio.run(_check())
    assert result is not None
    assert result.get("event") == "tasks.updated"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_web_controls.py::test_tasks_updated_sse_event_published_after_write -v`
Expected: FAIL — `_write_todo_file() got an unexpected keyword argument 'event_bus'`

- [ ] **Step 3: Add `event_bus` parameter to `_write_todo_file`**

In `src/devflow/cli.py:366`, change:

```python
def _write_todo_file(app_cfg: Config, tasks: list[Task], quiet: bool) -> None:
    """Generate/overwrite ``TODO.md`` from ``tasks`` sorted by priority."""
    from devflow.todo import generate_todo_from_source, write_todo

    path = Path(app_cfg.workflow.todo_path)
    items = generate_todo_from_source(tasks)
    header = (
        "# TODO\n\n"
        "> Сгенерировано `devflow-super list-tasks --todo`.\n"
    )
    write_todo(path, items, header=header)
```

to:

```python
def _write_todo_file(
    app_cfg: Config,
    tasks: list[Task],
    quiet: bool,
    event_bus: Any = None,
) -> None:
    """Generate/overwrite ``TASKS.md`` from ``tasks`` sorted by priority.

    When ``event_bus`` is provided (daemon mode), publishes a
    ``tasks.updated`` SSE event so the dashboard can refresh live.
    """
    from devflow.todo import generate_todo_from_source, write_todo

    path = Path(app_cfg.workflow.todo_path)
    items = generate_todo_from_source(tasks)
    header = (
        "# TASKS\n\n"
        "> Сгенерировано `devflow-super list-tasks --todo`.\n"
    )
    write_todo(path, items, header=header)
    if event_bus is not None:
        import logging
        event_bus.publish(
            "*",
            {"event": "tasks.updated", "path": str(path), "count": len(items)},
        )
        logging.getLogger(__name__).info("Published tasks.updated (%d items)", len(items))
```

Add `from typing import Any` to imports if not present in `cli.py`.

- [ ] **Step 4: Wire event_bus in daemon's runner (optional integration)**

In `src/devflow/daemon/runner.py`, the `run_all` method calls `generate_todo_from_source` indirectly. The primary path for TASKS regeneration is the CLI `list-tasks --todo` command. For the daemon, the scheduler's `task_run` job calls `run_all`, which doesn't regenerate TASKS.md. The regeneration happens via CLI. For daemon-triggered regeneration, add an `event_bus` reference to the runner and publish after any `write_todo` call. For now, the CLI path is the trigger — wire `event_bus` only if the daemon calls `_write_todo_file` (it does not currently). Document this as "CLI-triggered" for now.

**Note:** The `PUT /api/tasks/run` endpoint (Task 4 of the previous plan) could trigger regeneration in the future. For this task, the SSE event is published whenever `_write_todo_file` is called with an `event_bus`. The CLI doesn't pass `event_bus` (it's `None` by default), so no event in CLI mode — correct behavior (CLI doesn't have a dashboard listener). The daemon, when wired, will pass `event_bus`.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_web_controls.py::test_tasks_updated_sse_event_published_after_write -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/devflow/cli.py tests/unit/daemon/test_web_controls.py
git commit -m "feat(daemon): publish tasks.updated SSE event after TASKS.md regeneration"
```

---

### Task 5: Frontend — API client + types for providers and agent update

**Files:**
- Modify: `frontend/src/api/client.ts` (add `getProviders`, `updateAgent`)
- Modify: `frontend/src/api/types.ts` (add `ProviderSummary`, `AgentUpdate`)

**Interfaces:**
- Consumes: existing `getJson`/`putJson` helpers
- Produces: `getProviders()`, `updateAgent(name, AgentUpdate)`

- [ ] **Step 1: Add types to `frontend/src/api/types.ts`**

Append:

```typescript
// --- Control: providers + agent field update (Enhancements A) ---

export interface ProviderSummary {
  name: string
  type: string | null
}

export interface AgentUpdate {
  system_prompt?: string
  provider?: string
  model?: string
  temperature?: number
}
```

- [ ] **Step 2: Add client functions to `frontend/src/api/client.ts`**

Add imports for new types, then append the functions:

```typescript
// --- Control: providers + agent update (Enhancements A) ---
export const getProviders = () => getJson<ProviderSummary[]>('/providers')
export const updateAgent = (name: string, update: AgentUpdate) =>
  putJson<{ name: string; status: string }>(
    `/agents/${encodeURIComponent(name)}`,
    update,
  )
```

Update the import block at the top to include `AgentUpdate` and `ProviderSummary`.

- [ ] **Step 3: Verify typecheck**

Run: `cd frontend && npm run typecheck`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/api/types.ts
git commit -m "feat(frontend): add providers + agent update API client + types"
```

---

### Task 6: Frontend — CronBuilder component

**Files:**
- Create: `frontend/src/components/controls/CronBuilder.vue`
- Modify: `frontend/src/components/controls/ConfigTab.vue` (use CronBuilder)
- Modify: `frontend/src/style.css` (cron builder styles)

- [ ] **Step 1: Create `CronBuilder.vue`**

```vue
<script setup lang="ts">
import { computed, ref, watch } from 'vue'

const props = defineProps<{
  modelValue: string
  label: string
}>()
const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

type RepeatMode = 'daily' | 'weekdays' | 'weekend' | 'specificDays' | 'everyN'
const repeatMode = ref<RepeatMode>('weekdays')
const selectedDays = ref<number[]>([1, 2, 3, 4, 5]) // 0=Sun..6=Sat
const timeEntries = ref<string[]>(['09:00'])
const everyNValue = ref(30)
const everyNUnit = ref<'minutes' | 'hours'>('minutes')
const showHelp = ref(false)
const showRaw = ref(false)

// Parse a cron string into builder fields (best-effort).
function parseCron(cron: string): void {
  const parts = cron.trim().split(/\s+/)
  if (parts.length !== 5) return
  const [min, hour, , , dow] = parts
  // Every N minutes/hours: */N * * * *
  if (min.startsWith('*/') && hour === '*' && dow === '*') {
    repeatMode.value = 'everyN'
    everyNValue.value = parseInt(min.slice(2), 10)
    everyNUnit.value = 'minutes'
    return
  }
  // Every N hours: * */N * * *
  if (hour.startsWith('*/') && min === '0' && dow === '*') {
    repeatMode.value = 'everyN'
    everyNValue.value = parseInt(hour.slice(2), 10)
    everyNUnit.value = 'hours'
    return
  }
  // Parse times: min + hour → multiple HH:MM
  const mins = min.includes(',') ? min.split(',') : [min]
  const hours = hour.includes(',') ? hour.split(',') : [hour]
  const times: string[] = []
  for (const h of hours) {
    for (const m of mins) {
      times.push(`${h.padStart(2, '0')}:${m.padStart(2, '0')}`)
    }
  }
  timeEntries.value = times.sort()
  // Determine repeat mode from DOW
  if (dow === '*') {
    repeatMode.value = 'daily'
  } else if (dow === '1-5') {
    repeatMode.value = 'weekdays'
  } else if (dow === '6,0' || dow === '0,6') {
    repeatMode.value = 'weekend'
  } else {
    repeatMode.value = 'specificDays'
    selectedDays.value = dow.split(',').map((d) => parseInt(d, 10)).filter((n) => !isNaN(n))
  }
}

// Build cron from fields
const cronString = computed<string>(() => {
  if (repeatMode.value === 'everyN') {
    if (everyNUnit.value === 'minutes') {
      return `*/${everyNValue.value} * * * *`
    }
    return `0 */${everyNValue.value} * * *`
  }
  // Parse time entries into mins/hours
  const mins = [...new Set(timeEntries.value.map((t) => t.split(':')[1]))].sort()
  const hours = [...new Set(timeEntries.value.map((t) => t.split(':')[0]))].sort()
  const minPart = mins.join(',')
  const hourPart = hours.join(',')
  let dow = '*'
  if (repeatMode.value === 'daily') dow = '*'
  else if (repeatMode.value === 'weekdays') dow = '1-5'
  else if (repeatMode.value === 'weekend') dow = '6,0'
  else if (repeatMode.value === 'specificDays') {
    dow = selectedDays.value.sort().join(',')
  }
  return `${minPart} ${hourPart} * * ${dow}`
})

// Human-readable preview
const preview = computed<string>(() => {
  const times = timeEntries.value.join(', ')
  if (repeatMode.value === 'everyN') {
    return `Каждые ${everyNValue.value} ${everyNUnit.value === 'minutes' ? 'минут' : 'часов'}`
  }
  const dayText: Record<RepeatMode, string> = {
    daily: 'Ежедневно',
    weekdays: 'По будням',
    weekend: 'По выходным',
    specificDays: `В дни: ${selectedDays.value.map((d) => ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'][d]).join(', ')}`,
    everyN: '',
  }
  return `${dayText[repeatMode.value]} в ${times}`
})

watch(cronString, (val) => emit('update:modelValue', val))
watch(() => props.modelValue, (val) => parseCron(val), { immediate: true })

function addTime() {
  timeEntries.value.push('12:00')
}
function removeTime(idx: number) {
  timeEntries.value.splice(idx, 1)
}
</script>

<template>
  <div class="cron-builder">
    <label class="cron-label">{{ label }}</label>

    <fieldset>
      <legend>Повторение</legend>
      <label><input type="radio" v-model="repeatMode" value="daily" /> Ежедневно</label>
      <label><input type="radio" v-model="repeatMode" value="weekdays" /> По будням (пн–пт)</label>
      <label><input type="radio" v-model="repeatMode" value="weekend" /> По выходным (сб–вс)</label>
      <label><input type="radio" v-model="repeatMode" value="specificDays" /> Конкретные дни</label>
      <div v-if="repeatMode === 'specificDays'" class="day-checkboxes">
        <label v-for="(day, idx) in ['Вс','Пн','Вт','Ср','Чт','Пт','Сб']" :key="idx">
          <input type="checkbox" :value="idx" v-model="selectedDays" /> {{ day }}
        </label>
      </div>
      <label><input type="radio" v-model="repeatMode" value="everyN" /> Каждые</label>
      <span v-if="repeatMode === 'everyN'" class="every-n">
        <input type="number" v-model.number="everyNValue" min="1" />
        <select v-model="everyNUnit">
          <option value="minutes">минут</option>
          <option value="hours">часов</option>
        </select>
      </span>
    </fieldset>

    <fieldset v-if="repeatMode !== 'everyN'">
      <legend>Время</legend>
      <div v-for="(t, idx) in timeEntries" :key="idx" class="time-entry">
        <input type="time" v-model="timeEntries[idx]" />
        <button @click="removeTime(idx)" v-if="timeEntries.length > 1" class="remove-time">×</button>
      </div>
      <button @click="addTime" class="add-time">+ Добавить время</button>
    </fieldset>

    <div class="cron-preview">
      <strong>Preview:</strong> {{ preview }}
      <br />
      <small>Raw: <code>{{ cronString }}</code></small>
      <button @click="showHelp = !showHelp" class="help-toggle">{{ showHelp ? '▾' : '▸' }} Справка по cron</button>
    </div>

    <details v-if="showHelp" class="cron-help">
      <summary>Формат cron (5 полей)</summary>
      <p>Cron состоит из 5 полей, разделённых пробелами:</p>
      <ul>
        <li><code>минута</code> (0–59)</li>
        <li><code>час</code> (0–23)</li>
        <li><code>день месяца</code> (1–31)</li>
        <li><code>месяц</code> (1–12 или JAN–DEC)</li>
        <li><code>день недели</code> (0–6, где 0=воскресенье, или SUN–SAT)</li>
      </ul>
      <p>Спецсимволы: <code>*</code> (любое), <code>,</code> (список), <code>-</code> (диапазон), <code>/</code> (шаг).</p>
      <p>Alias: <code>@daily</code> (0 0 * * *), <code>@hourly</code> (0 * * * *), <code>@weekly</code> (0 0 * * 0).</p>
      <p>Пример: <code>0 9,15 * * 1-5</code> = по будням в 09:00 и 15:00.</p>
    </details>
  </div>
</template>
```

- [ ] **Step 2: Add cron builder styles to `frontend/src/style.css`**

Append:

```css
/* --- Cron builder --- */
.cron-builder {
  margin-bottom: 1rem;
}
.cron-builder fieldset {
  border: 1px solid #e1e1e3;
  border-radius: 4px;
  margin-bottom: 0.5rem;
  padding: 0.5rem;
}
.cron-builder legend {
  font-size: 0.85rem;
  font-weight: 600;
  color: #555;
}
.cron-builder label {
  display: inline-block;
  margin-right: 1rem;
}
.day-checkboxes, .every-n, .time-entry {
  margin-top: 0.3rem;
  padding-left: 1.5rem;
}
.time-entry {
  margin-bottom: 0.3rem;
}
.add-time, .remove-time, .help-toggle {
  font-size: 0.85rem;
  padding: 0.15rem 0.5rem;
}
.cron-preview {
  background: #f7f7f8;
  padding: 0.5rem;
  border-radius: 4px;
  margin-top: 0.5rem;
}
.cron-help {
  margin-top: 0.5rem;
  font-size: 0.85rem;
}
```

- [ ] **Step 3: Use CronBuilder in ConfigTab**

In `frontend/src/components/controls/ConfigTab.vue`, replace the schedule `<input>` blocks. Replace:

```vue
        <dt>Task schedule (cron)</dt>
        <dd>
          <input
            :value="config.config.daemon.task_schedule"
            @change="onPatchSchedule('task_schedule', ($event.target as HTMLInputElement).value)"
          />
        </dd>
        <dt>EOD schedule (cron)</dt>
        <dd>
          <input
            :value="config.config.daemon.eod_schedule"
            @change="onPatchSchedule('eod_schedule', ($event.target as HTMLInputElement).value)"
          />
        </dd>
```

with:

```vue
        <dt>Task schedule</dt>
        <dd>
          <CronBuilder
            :modelValue="config.config.daemon.task_schedule"
            @update:modelValue="onPatchSchedule('task_schedule', $event)"
            label="Task schedule"
          />
        </dd>
        <dt>EOD schedule</dt>
        <dd>
          <CronBuilder
            :modelValue="config.config.daemon.eod_schedule"
            @update:modelValue="onPatchSchedule('eod_schedule', $event)"
            label="EOD schedule"
          />
        </dd>
```

Add the import at the top of the `<script setup>`:

```typescript
import CronBuilder from '@/components/controls/CronBuilder.vue'
```

- [ ] **Step 4: Verify typecheck + build**

Run: `cd frontend && npm run typecheck && npm run build`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/controls/CronBuilder.vue frontend/src/components/controls/ConfigTab.vue frontend/src/style.css
git commit -m "feat(frontend): graphical CronBuilder component replaces raw cron input"
```

---

### Task 7: Frontend — TasksTab (rename TodoTab) with refresh + SSE

**Files:**
- Rename: `frontend/src/components/controls/TodoTab.vue` → `TasksTab.vue` (+ content changes)
- Modify: `frontend/src/stores/todo.ts` (add `lastUpdated`)
- Modify: `frontend/src/composables/useSSE.ts` (add `tasks.updated` listener)
- Modify: `frontend/src/views/ControlsView.vue` (tab label)
- Modify: `frontend/src/style.css` (refresh button styles)

- [ ] **Step 1: Update `todo.ts` store with `lastUpdated`**

In `frontend/src/stores/todo.ts`, add a `lastUpdated` ref and update it on fetch:

```typescript
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getTodo, patchTodo } from '@/api/client'
import type { TodoItem, TodoPatch } from '@/api/types'

export const useTodoStore = defineStore('todo-control', () => {
  const items = ref<TodoItem[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const lastUpdated = ref<Date | null>(null)

  async function fetch() {
    loading.value = true
    error.value = null
    try {
      items.value = await getTodo()
      lastUpdated.value = new Date()
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

  return { items, loading, error, lastUpdated, fetch, updateLine }
})
```

- [ ] **Step 2: Create `TasksTab.vue` (rename from TodoTab + refresh UI)**

Delete `TodoTab.vue` and create `TasksTab.vue`:

```vue
<script setup lang="ts">
import { onMounted, onBeforeUnmount } from 'vue'
import { useTodoStore } from '@/stores/todo'
import { usePolling } from '@/composables/usePolling'

const todo = useTodoStore()
const { start, stop } = usePolling(() => todo.fetch(), 30000)
onMounted(() => {
  void todo.fetch()
  start()
})
onBeforeUnmount(() => stop())

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
    <div class="tasks-header">
      <h3>TASKS</h3>
      <div class="refresh-section">
        <button @click="todo.fetch()" :disabled="todo.loading" class="refresh-btn">
          ↻ {{ todo.loading ? 'Загрузка…' : 'Обновить' }}
        </button>
        <small v-if="todo.lastUpdated" class="last-updated">
          Обновлено: {{ todo.lastUpdated.toLocaleTimeString() }}
        </small>
      </div>
    </div>
    <p v-if="todo.error" class="error">{{ todo.error }}</p>
    <table v-if="todo.items.some(i => i.checkbox !== null)">
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
              v-if="it.priority !== null"
              :value="it.priority"
              :class="priorityClass(it.priority)"
              @change="changePriority(it.line_no, Number(($event.target as HTMLSelectElement).value))"
            >
              <option v-for="n in 6" :key="n - 1" :value="n - 1">#r{{ n - 1 }}</option>
            </select>
            <span v-else class="prio-none">—</span>
          </td>
          <td>{{ it.title }}</td>
        </tr>
      </tbody>
    </table>
    <p v-else>Нет задач.</p>
  </section>
</template>
```

- [ ] **Step 3: Add `tasks.updated` listener to `useSSE.ts`**

In `frontend/src/composables/useSSE.ts`, add import and listener. After the `eod.ready` listener, add:

```typescript
    source.addEventListener('tasks.updated', () => {
      void todo.fetch()
    })
```

Add the import of the todo store:

```typescript
import { useTodoStore } from '@/stores/todo'
```

And inside `connect()`:

```typescript
  const todo = useTodoStore()
```

- [ ] **Step 4: Update ControlsView to use TasksTab**

In `frontend/src/views/ControlsView.vue`, change the import and reference:

```typescript
import TasksTab from '@/components/controls/TasksTab.vue'
```

And in the template, change `<TodoTab />` to `<TasksTab />`, and the tab label from `'TODO'` to `'TASKS'`:

```typescript
const tabs: { id: TabId; label: string }[] = [
  { id: 'run', label: 'Run' },
  { id: 'todo', label: 'TASKS' },  // id stays 'todo' to avoid router churn
  { id: 'config', label: 'Config' },
  { id: 'agents', label: 'Agents' },
]
```

And:

```vue
    <TasksTab v-else-if="activeTab === 'todo'" />
```

- [ ] **Step 5: Add refresh button styles to `style.css`**

Append:

```css
/* --- Tasks refresh --- */
.tasks-header { display: flex; justify-content: space-between; align-items: center; }
.refresh-section { text-align: right; }
.refresh-btn { font-size: 0.9rem; }
.last-updated { display: block; color: #999; font-size: 0.75rem; }
.prio-none { color: #ccc; }
```

- [ ] **Step 6: Verify typecheck + build**

Run: `cd frontend && npm run typecheck && npm run build`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/controls/TasksTab.vue frontend/src/stores/todo.ts frontend/src/composables/useSSE.ts frontend/src/views/ControlsView.vue frontend/src/style.css
git rm frontend/src/components/controls/TodoTab.vue
git commit -m "feat(frontend): TasksTab with refresh button + SSE tasks.updated + 30s polling"
```

---

### Task 8: Frontend — AgentsTab provider/model/temperature editing

**Files:**
- Modify: `frontend/src/components/controls/AgentsTab.vue`
- Modify: `frontend/src/stores/agents.ts` (providers list)

- [ ] **Step 1: Update `agents.ts` store with providers**

In `frontend/src/stores/agents.ts`, add providers loading. Add import and state:

```typescript
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getAgent, getAgents, getProviders, saveAgent, updateAgent, updateAgentPrompt } from '@/api/client'
import type { AgentDetail, AgentSummary, ProviderSummary } from '@/api/types'

export const useAgentsStore = defineStore('agents-control', () => {
  const agents = ref<AgentSummary[]>([])
  const providers = ref<ProviderSummary[]>([])
  const current = ref<AgentDetail | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const modified = ref<Set<string>>(new Set())

  async function fetchList() {
    loading.value = true
    error.value = null
    try {
      const [a, p] = await Promise.all([getAgents(), getProviders()])
      agents.value = a
      providers.value = p
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

  async function updateFields(name: string, update: { system_prompt?: string; provider?: string; model?: string; temperature?: number }) {
    error.value = null
    try {
      await updateAgent(name, update)
      if (current.value && current.value.name === name) {
        if (update.system_prompt !== undefined) current.value.system_prompt = update.system_prompt
        if (update.provider !== undefined) current.value.provider = update.provider
        if (update.model !== undefined) current.value.model = update.model
        if (update.temperature !== undefined) current.value.temperature = update.temperature
      }
      modified.value.add(name)
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      throw e
    }
  }

  // Keep old updatePrompt as alias for backwards compat.
  async function updatePrompt(name: string, prompt: string) {
    return updateFields(name, { system_prompt: prompt })
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

  return { agents, providers, current, loading, error, modified, fetchList, select, updateFields, updatePrompt, save }
})
```

- [ ] **Step 2: Update `AgentsTab.vue` with editable provider/model/temperature**

Replace the `<dl>` frontmatter block (read-only) in `AgentsTab.vue`. Replace:

```vue
        <dl>
          <dt>Provider</dt><dd>{{ agents.current.provider }}</dd>
          <dt>Model</dt><dd>{{ agents.current.model }}</dd>
          <dt>Temperature</dt><dd>{{ agents.current.temperature }}</dd>
        </dl>
```

with:

```vue
        <dl>
          <dt>Provider</dt>
          <dd>
            <select
              :value="agents.current.provider"
              @change="onFieldChange('provider', ($event.target as HTMLSelectElement).value)"
            >
              <option v-for="p in agents.providers" :key="p.name" :value="p.name">{{ p.name }}</option>
            </select>
          </dd>
          <dt>Model</dt>
          <dd>
            <input
              type="text"
              :value="agents.current.model"
              @change="onFieldChange('model', ($event.target as HTMLInputElement).value)"
            />
          </dd>
          <dt>Temperature</dt>
          <dd>
            <input
              type="number"
              step="0.1"
              min="0"
              max="2"
              :value="agents.current.temperature"
              @change="onFieldChange('temperature', Number(($event.target as HTMLInputElement).value))"
            />
          </dd>
        </dl>
```

Add the `onFieldChange` handler in the `<script setup>`:

```typescript
let fieldTimer: ReturnType<typeof setTimeout> | null = null

function onFieldChange(field: 'provider' | 'model' | 'temperature', value: string | number) {
  if (!agents.current) return
  if (fieldTimer) clearTimeout(fieldTimer)
  fieldTimer = setTimeout(() => {
    void agents.updateFields(agents.current!.name, { [field]: value })
  }, 1000)
}
```

- [ ] **Step 3: Verify typecheck + build**

Run: `cd frontend && npm run typecheck && npm run build`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/src/stores/agents.ts frontend/src/components/controls/AgentsTab.vue
git commit -m "feat(frontend): editable provider/model/temperature in AgentsTab"
```

---

### Task 9: Smoke test via Playwright + final verification

**Files:** None (verification only)

- [ ] **Step 1: Rebuild frontend + restart daemon**

```bash
cd frontend && npm run build && cd ..
# Kill + restart daemon
```

- [ ] **Step 2: Verify endpoints**

```bash
curl -s http://127.0.0.1:8787/api/providers
curl -s http://127.0.0.1:8787/api/agents | head -c 300
```

- [ ] **Step 3: Playwright smoke test**

Navigate to `/controls`. Verify:
- TASKS tab shows refresh button + last-updated indicator
- Config tab shows CronBuilder (not raw input)
- Agents tab shows editable provider dropdown + model field

- [ ] **Step 4: Full test suite**

```bash
python -m pytest tests/ -q
cd frontend && npm run typecheck && npm run build
```

- [ ] **Step 5: Commit if needed**

---

## Self-Review

**1. Spec coverage:**
- ✅ Priority mapping (Немедленный→r0, etc., None for unknown) → Task 1
- ✅ Rename TODO.md → TASKS.md + fallback → Task 2
- ✅ Refresh (button + SSE + polling) → Task 7 (frontend) + Task 4 (SSE backend)
- ✅ Cron builder → Task 6
- ✅ Providers/models editing → Tasks 3 (backend) + 8 (frontend)

**2. Placeholder scan:** No TBD/TODO. All steps have code. ✅

**3. Type consistency:**
- `priority_from_task() -> int | None` consistent across Task 1 tests + impl ✅
- `getProviders()` / `ProviderSummary` consistent across Tasks 3, 5, 8 ✅
- `updateAgent(name, AgentUpdate)` consistent across Tasks 3, 5, 8 ✅
- `migrate_todo_to_tasks(config_dir)` consistent across Task 2 ✅
