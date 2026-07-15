# Dashboard Enhancements B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an LLM-driven execution queue — a prioritizer node evaluates TASKS + code context, stores the optimal order in SQLite, and humans reorder it via drag-and-drop + up/down buttons in the dashboard.

**Architecture:** New `QueueStore` (SQLite, mirrors BatchStore pattern). New `prioritizer_node` inserted before `orchestrator` in the graph. New `/api/queue/*` endpoints. New `QueuePanel.vue` in the Run tab with native HTML5 drag-and-drop + ↑↓ buttons. SSE `queue.updated` event refreshes the UI after each LLM re-evaluation.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, LangGraph, SQLite, pytest (backend); Vue 3 Composition API, TypeScript, Pinia, Vite (frontend).

## Global Constraints

- **QueueStore** mirrors `BatchStore` pattern: `sqlite3.connect(check_same_thread=False)`, `_create_schema()`, JSON-safe Pydantic models.
- **DB path**: `.devflow/queue.db` (alongside `batch_store.db`).
- **Prioritizer node** is inserted between `START` and `orchestrator`: `START → prioritizer → orchestrator`.
- **Fallback**: if prioritizer fails (LLM error, empty TASKS), orchestrator uses `select_next_todo` (existing logic).
- **Manual reorder** via `threading.Lock` in QueueStore (same rationale as BatchStore: cross-thread access).
- **D&D** is native HTML5 Drag and Drop API — NO external libraries.
- **SSE event**: `queue.updated` published after `set_queue` and after manual reorders.
- **Auto re-evaluate**: every `run_task`/cron cycle starts the graph → prioritizer runs first.
- **Backend tests**: pytest in `tests/unit/`, follow BatchStore test patterns.
- **Frontend**: no test runner this round; verify via `npm run typecheck` + `npm run build`.
- **Commit after each task** — conventional commits.
- **Branch**: `feature/phase5-vue-dashboard`.

---

## File Structure

**Backend — new:**
- `src/devflow/batch/queue_store.py` — QueueStore (SQLite CRUD + reorder)
- `src/devflow/nodes/prioritizer.py` — LLM prioritizer node
- `config/agents/prioritizer.md` — agent prompt
- `tests/unit/batch/test_queue_store.py`
- `tests/unit/nodes/test_prioritizer.py`
- `tests/unit/daemon/test_web_queue.py`

**Backend — modified:**
- `src/devflow/graph.py` — add prioritizer node + entry edge
- `src/devflow/daemon/__main__.py` — QueueStore construction + wiring
- `src/devflow/daemon/web.py` — `/api/queue/*` endpoints + SSE publish
- `src/devflow/daemon/runner.py` — pass queue_store to graph build
- `src/devflow/schemas.py` — `PrioritizationResult` model

**Frontend — new:**
- `frontend/src/components/controls/QueuePanel.vue`
- `frontend/src/stores/queue.ts`

**Frontend — modified:**
- `frontend/src/components/controls/RunTab.vue` — integrate QueuePanel
- `frontend/src/api/client.ts` — queue API functions
- `frontend/src/api/types.ts` — QueueEntry type
- `frontend/src/composables/useSSE.ts` — `queue.updated` listener
- `frontend/src/style.css` — queue panel + d&d styles

---

### Task 1: QueueStore (SQLite persistent storage)

**Files:**
- Create: `src/devflow/batch/queue_store.py`
- Test: `tests/unit/batch/test_queue_store.py`

**Interfaces:**
- Consumes: Pydantic `BaseModel`, `sqlite3`, `threading.Lock`
- Produces: `QueueEntry` model, `QueueStore` class with `get_queue/set_queue/reorder/move_up/move_down/next_task_id/remove/clear`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/batch/test_queue_store.py`:

```python
"""Tests for QueueStore — SQLite-backed execution queue."""

from __future__ import annotations

from pathlib import Path

from devflow.batch.queue_store import QueueEntry, QueueStore


def _make_store(tmp_path: Path) -> QueueStore:
    return QueueStore(str(tmp_path / "queue.db"))


def _entry(task_id: str, title: str = "Task", priority: int | None = 3) -> QueueEntry:
    return QueueEntry(
        position=0,
        task_id=task_id,
        task_title=title,
        priority=priority,
        reason="",
        updated_at="2026-01-01T00:00:00",
    )


def test_set_and_get_queue_round_trip(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    entries = [
        _entry("1", "First", 0),
        _entry("2", "Second", 1),
    ]
    store.set_queue(entries)
    result = store.get_queue()
    assert len(result) == 2
    assert result[0].task_id == "1"
    assert result[0].position == 0
    assert result[1].task_id == "2"
    assert result[1].position == 1


def test_set_queue_overwrites_previous(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.set_queue([_entry("1"), _entry("2"), _entry("3")])
    store.set_queue([_entry("4"), _entry("5")])
    result = store.get_queue()
    assert len(result) == 2
    assert result[0].task_id == "4"


def test_reorder_moves_task_to_new_position(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.set_queue([_entry("1"), _entry("2"), _entry("3")])
    # Move task "3" to position 0.
    result = store.reorder("3", 0)
    assert [e.task_id for e in result] == ["3", "1", "2"]
    # Positions are sequential 0..N-1.
    assert [e.position for e in result] == [0, 1, 2]


def test_move_up(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.set_queue([_entry("1"), _entry("2"), _entry("3")])
    result = store.move_up("3")
    assert [e.task_id for e in result] == ["1", "3", "2"]


def test_move_up_first_is_noop(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.set_queue([_entry("1"), _entry("2")])
    result = store.move_up("1")
    assert [e.task_id for e in result] == ["1", "2"]


def test_move_down(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.set_queue([_entry("1"), _entry("2"), _entry("3")])
    result = store.move_down("1")
    assert [e.task_id for e in result] == ["2", "1", "3"]


def test_move_down_last_is_noop(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.set_queue([_entry("1"), _entry("2")])
    result = store.move_down("2")
    assert [e.task_id for e in result] == ["1", "2"]


def test_next_task_id_returns_first(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.set_queue([_entry("1"), _entry("2")])
    assert store.next_task_id() == "1"


def test_next_task_id_empty_returns_none(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    assert store.next_task_id() is None


def test_remove_task(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.set_queue([_entry("1"), _entry("2"), _entry("3")])
    store.remove("2")
    result = store.get_queue()
    assert [e.task_id for e in result] == ["1", "3"]


def test_reorder_unknown_task_raises(tmp_path: Path) -> None:
    import pytest
    store = _make_store(tmp_path)
    store.set_queue([_entry("1")])
    with pytest.raises(KeyError):
        store.reorder("bogus", 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/batch/test_queue_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'devflow.batch.queue_store'`

- [ ] **Step 3: Write QueueStore implementation**

Create `src/devflow/batch/queue_store.py`:

```python
"""SQLite-backed execution queue.

The queue is the LLM-evaluated execution order of tasks (separate from
TASKS.md which is the raw task list). The prioritizer node writes here;
humans reorder via the dashboard (drag-and-drop / up-down buttons).

The DB file lives at ``{repo_path}/.devflow/queue.db``. Thread-safe via a
``threading.Lock`` (same rationale as BatchStore: cross-thread access from
graph node + FastAPI handlers + APScheduler).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class QueueEntry(BaseModel):
    """A single entry in the execution queue."""

    position: int
    task_id: str
    task_title: str = ""
    priority: int | None = None
    reason: str = ""
    updated_at: str = ""


class QueueStore:
    """CRUD + reorder for the execution queue in SQLite."""

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
            CREATE TABLE IF NOT EXISTS execution_queue (
                position INTEGER PRIMARY KEY,
                task_id TEXT NOT NULL UNIQUE,
                task_title TEXT DEFAULT '',
                priority INTEGER,
                reason TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            )
            """
        )
        self._conn.commit()

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> QueueEntry:
        return QueueEntry(
            position=row["position"],
            task_id=row["task_id"],
            task_title=row["task_title"] or "",
            priority=row["priority"],
            reason=row["reason"] or "",
            updated_at=row["updated_at"] or "",
        )

    def get_queue(self) -> list[QueueEntry]:
        """Return all entries ordered by position."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM execution_queue ORDER BY position"
            ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def set_queue(self, entries: list[QueueEntry]) -> None:
        """Overwrite the entire queue (used by the prioritizer node)."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute("DELETE FROM execution_queue")
            for i, e in enumerate(entries):
                self._conn.execute(
                    "INSERT INTO execution_queue (position, task_id, task_title, priority, reason, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (i, e.task_id, e.task_title, e.priority, e.reason, now),
                )
            self._conn.commit()

    def _rewrite_order(self, ordered_ids: list[str]) -> list[QueueEntry]:
        """Rewrite positions for the given task_id order. Caller holds lock."""
        now = datetime.now(timezone.utc).isoformat()
        rows = {
            r["task_id"]: r for r in self._conn.execute("SELECT * FROM execution_queue").fetchall()
        }
        self._conn.execute("DELETE FROM execution_queue")
        for i, tid in enumerate(ordered_ids):
            r = rows[tid]
            self._conn.execute(
                "INSERT INTO execution_queue (position, task_id, task_title, priority, reason, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (i, tid, r["task_title"], r["priority"], r["reason"], now),
            )
        self._conn.commit()
        return self.get_queue()

    def reorder(self, task_id: str, new_position: int) -> list[QueueEntry]:
        """Move task_id to new_position, shifting others. Returns updated queue."""
        with self._lock:
            current = [
                r["task_id"]
                for r in self._conn.execute(
                    "SELECT task_id FROM execution_queue ORDER BY position"
                ).fetchall()
            ]
            if task_id not in current:
                raise KeyError(f"Task {task_id} not in queue")
            if not (0 <= new_position < len(current)):
                raise ValueError(f"new_position {new_position} out of range 0..{len(current) - 1}")
            current.remove(task_id)
            current.insert(new_position, task_id)
            return self._rewrite_order(current)

    def move_up(self, task_id: str) -> list[QueueEntry]:
        """Swap task_id with the one above it. Noop if already first."""
        with self._lock:
            current = [
                r["task_id"]
                for r in self._conn.execute(
                    "SELECT task_id FROM execution_queue ORDER BY position"
                ).fetchall()
            ]
            if task_id not in current:
                raise KeyError(f"Task {task_id} not in queue")
            idx = current.index(task_id)
            if idx > 0:
                current[idx], current[idx - 1] = current[idx - 1], current[idx]
                return self._rewrite_order(current)
            return self.get_queue()

    def move_down(self, task_id: str) -> list[QueueEntry]:
        """Swap task_id with the one below it. Noop if already last."""
        with self._lock:
            current = [
                r["task_id"]
                for r in self._conn.execute(
                    "SELECT task_id FROM execution_queue ORDER BY position"
                ).fetchall()
            ]
            if task_id not in current:
                raise KeyError(f"Task {task_id} not in queue")
            idx = current.index(task_id)
            if idx < len(current) - 1:
                current[idx], current[idx + 1] = current[idx + 1], current[idx]
                return self._rewrite_order(current)
            return self.get_queue()

    def next_task_id(self) -> str | None:
        """Return the task_id at position 0, or None if queue is empty."""
        with self._lock:
            row = self._conn.execute(
                "SELECT task_id FROM execution_queue ORDER BY position LIMIT 1"
            ).fetchone()
        return row["task_id"] if row else None

    def remove(self, task_id: str) -> None:
        """Remove a task from the queue."""
        with self._lock:
            self._conn.execute("DELETE FROM execution_queue WHERE task_id = ?", (task_id,))
            self._conn.commit()

    def clear(self) -> None:
        """Remove all entries."""
        with self._lock:
            self._conn.execute("DELETE FROM execution_queue")
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/batch/test_queue_store.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add src/devflow/batch/queue_store.py tests/unit/batch/test_queue_store.py
git commit -m "feat(batch): QueueStore — SQLite-backed execution queue with reorder"
```

---

### Task 2: PrioritizationResult schema + prioritizer agent prompt

**Files:**
- Modify: `src/devflow/schemas.py` — add `PrioritizedTask`, `PrioritizationResult`
- Create: `config/agents/prioritizer.md`

**Interfaces:**
- Consumes: Pydantic BaseModel (existing schemas pattern)
- Produces: `PrioritizedTask`, `PrioritizationResult` for LLM structured output

- [ ] **Step 1: Add schema models**

Append to `src/devflow/schemas.py`:

```python
class PrioritizedTask(BaseModel):
    """A single task in the LLM-evaluated execution order."""

    task_id: str
    reason: str = ""


class PrioritizationResult(BaseModel):
    """LLM output: ordered list of task IDs with justifications."""

    ordered_tasks: list[PrioritizedTask]
    notes: str = ""
```

- [ ] **Step 2: Create the prioritizer agent prompt**

Create `config/agents/prioritizer.md`:

```markdown
---
name: prioritizer
provider: openai
model: GLM-5.2
temperature: 0.2
---

# Role
You are a task prioritization specialist for an autonomous software development workflow.

# Instructions
Given a list of tasks (with titles, priorities from r0=critical to r5=lowest)
and a summary of the current repository state, determine the optimal execution order.

Consider:
- Task dependencies (does task A unblock task B?)
- Priority level (r0 first, r5 last)
- Estimated complexity (simpler tasks first to build momentum)
- Code areas touched (group related tasks to reduce context switching)

Output a JSON object with an ordered list of task IDs in recommended execution
order (first = next to execute). Include a brief reason for the overall ordering.
```

- [ ] **Step 3: Commit**

```bash
git add src/devflow/schemas.py config/agents/prioritizer.md
git commit -m "feat(schemas): PrioritizationResult + prioritizer agent prompt"
```

---

### Task 3: Prioritizer node (LLM evaluation)

**Files:**
- Create: `src/devflow/nodes/prioritizer.py`
- Test: `tests/unit/nodes/test_prioritizer.py`

**Interfaces:**
- Consumes: `QueueStore`, `parse_todo`, `call_structured`, `build_llm`, `app_cfg`, `repo_path`
- Produces: `prioritizer_node(state, *, app_cfg, repo_path, queue_store, event_bus=None) -> dict` — writes queue, returns state dict (no task, just logs)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/nodes/test_prioritizer.py`:

```python
"""Tests for the LLM prioritizer node."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from devflow.batch.queue_store import QueueStore
from devflow.schemas import PrioritizedTask, PrioritizationResult


def test_prioritizer_writes_queue_from_llm_output(tmp_path: Path) -> None:
    """Prioritizer node writes the LLM-ordered tasks into QueueStore."""
    from devflow.nodes.prioritizer import prioritizer_node

    # Write a TASKS.md with 3 tasks.
    tasks_md = tmp_path / "TASKS.md"
    tasks_md.write_text(
        "# TASKS\n"
        "- [ ] #r0 #111 — Task A\n"
        "- [ ] #r1 #222 — Task B\n"
        "- [ ] #r3 #333 — Task C\n",
        encoding="utf-8",
    )

    store = QueueStore(str(tmp_path / "queue.db"))
    cfg = MagicMock()
    cfg.workflow.todo_path = str(tasks_md)
    cfg.agents = {"prioritizer": MagicMock(system_prompt="You prioritize.")}

    llm_result = PrioritizationResult(
        ordered_tasks=[
            PrioritizedTask(task_id="222", reason="Unblocks others"),
            PrioritizedTask(task_id="111", reason="Critical but depends on 222"),
            PrioritizedTask(task_id="333", reason="Independent, low priority"),
        ]
    )

    with patch("devflow.nodes.prioritizer.build_llm") as mock_build, \
         patch("devflow.nodes.prioritizer.call_structured") as mock_call:
        mock_call.return_value = llm_result
        result = prioritizer_node(
            state={},
            app_cfg=cfg,
            repo_path=str(tmp_path),
            queue_store=store,
        )

    queue = store.get_queue()
    assert [e.task_id for e in queue] == ["222", "111", "333"]
    assert queue[0].reason == "Unblocks others"
    assert "logs" in result


def test_prioritizer_fallback_on_llm_error(tmp_path: Path) -> None:
    """If LLM call fails, the queue stays empty (orchestrator falls back to select_next_todo)."""
    from devflow.nodes.prioritizer import prioritizer_node

    tasks_md = tmp_path / "TASKS.md"
    tasks_md.write_text("- [ ] #r0 #111 — Task A\n", encoding="utf-8")

    store = QueueStore(str(tmp_path / "queue.db"))
    cfg = MagicMock()
    cfg.workflow.todo_path = str(tasks_md)
    cfg.agents = {"prioritizer": MagicMock(system_prompt="x")}

    with patch("devflow.nodes.prioritizer.call_structured", side_effect=RuntimeError("LLM down")):
        result = prioritizer_node(
            state={},
            app_cfg=cfg,
            repo_path=str(tmp_path),
            queue_store=store,
        )

    # Queue should be empty (fallback).
    assert store.get_queue() == []
    assert "prioritizer failed" in result["logs"][0].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/nodes/test_prioritizer.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the prioritizer node**

Create `src/devflow/nodes/prioritizer.py`:

```python
"""LLM prioritizer node — evaluates TASKS.md and writes the execution queue.

Inserted before the orchestrator in the graph. Reads TASKS.md, asks the LLM
for an optimal execution order (considering priorities, dependencies, code
context), and writes the result to QueueStore. On failure, the queue stays
empty and the orchestrator falls back to ``select_next_todo``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from devflow.batch.queue_store import QueueEntry, QueueStore
from devflow.config import Config
from devflow.llm_factory import build_llm
from devflow.schemas import PrioritizationResult
from devflow.todo import parse_todo
from devflow.utils import call_structured

logger = logging.getLogger(__name__)


def prioritizer_node(
    state: dict[str, Any],
    *,
    app_cfg: Config,
    repo_path: str,
    queue_store: QueueStore,
    event_bus: Any | None = None,
) -> dict[str, Any]:
    """Evaluate TASKS.md via LLM and write the execution queue.

    This node does NOT select a task (that's the orchestrator's job). It only
    writes the ordered queue so the orchestrator can pick ``next_task_id()``.
    """
    todo_path = Path(app_cfg.workflow.todo_path)
    if not todo_path.exists():
        logger.info("Prioritizer: no TASKS.md at %s, skipping", todo_path)
        return {"logs": ["prioritizer: no TASKS.md, skipped"]}

    items = parse_todo(todo_path)
    # Only actionable tasks: checkbox [ ] + has a task ref.
    candidates = [
        it for it in items
        if it.checkbox == "[ ]" and it.task_ref is not None
    ]
    if not candidates:
        logger.info("Prioritizer: no actionable tasks in TASKS.md")
        return {"logs": ["prioritizer: no actionable tasks"]}

    # Build the LLM prompt input: task list with priorities.
    task_lines = []
    for it in candidates:
        prio = f"r{it.priority}" if it.priority is not None else "none"
        task_lines.append(f"- [{prio}] #{it.task_ref} — {it.title}")

    agent_cfg = app_cfg.agents.get("prioritizer")
    if agent_cfg is None:
        logger.warning("Prioritizer: no 'prioritizer' agent configured, skipping")
        return {"logs": ["prioritizer: no agent configured, skipped"]}

    llm = build_llm(agent_cfg, app_cfg)
    prompt = (
        f"Tasks to prioritize:\n{''.join(chr(10).join(task_lines))}\n\n"
        f"Determine the optimal execution order. Return ordered task IDs."
    )

    try:
        result: PrioritizationResult = call_structured(
            llm, agent_cfg, PrioritizationResult, prompt
        )
    except Exception as exc:
        logger.exception("Prioritizer LLM call failed")
        return {"logs": [f"prioritizer: failed ({exc}), orchestrator will use select_next_todo"]}

    # Write the queue: map LLM-ordered task_ids back to TodoItems for metadata.
    item_map = {it.task_ref: it for it in candidates}
    entries: list[QueueEntry] = []
    for pt in result.ordered_tasks:
        it = item_map.get(pt.task_id)
        entries.append(
            QueueEntry(
                position=0,  # set_queue assigns real positions
                task_id=pt.task_id,
                task_title=it.title if it else "",
                priority=it.priority if it else None,
                reason=pt.reason,
                updated_at="",
            )
        )
    # Also append any tasks the LLM missed (safety net).
    seen = {pt.task_id for pt in result.ordered_tasks}
    for it in candidates:
        if it.task_ref not in seen:
            entries.append(
                QueueEntry(
                    position=0,
                    task_id=it.task_ref or "",
                    task_title=it.title,
                    priority=it.priority,
                    reason="not evaluated by LLM",
                    updated_at="",
                )
            )

    queue_store.set_queue(entries)
    logger.info("Prioritizer wrote %d tasks to queue", len(entries))

    # Publish SSE event so the dashboard refreshes.
    if event_bus is not None:
        import asyncio
        try:
            asyncio.run(event_bus.publish("*", {"event": "queue.updated", "count": len(entries)}))
        except RuntimeError:
            pass

    return {"logs": [f"prioritizer: wrote {len(entries)} tasks to queue"]}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/nodes/test_prioritizer.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/devflow/nodes/prioritizer.py tests/unit/nodes/test_prioritizer.py
git commit -m "feat(nodes): LLM prioritizer node — evaluates TASKS + writes QueueStore"
```

---

### Task 4: Wire prioritizer into graph + daemon + runner

**Files:**
- Modify: `src/devflow/graph.py` — add prioritizer node + entry edge
- Modify: `src/devflow/daemon/__main__.py` — construct QueueStore + pass to runner
- Modify: `src/devflow/daemon/runner.py` — accept + forward queue_store

- [ ] **Step 1: Add prioritizer to graph.py**

In `src/devflow/graph.py`, add import:

```python
from devflow.nodes.prioritizer import prioritizer_node
```

In `build_graph`, add a `queue_store` parameter and the node. Change the signature:

```python
def build_graph(
    app_cfg: Config,
    repo_path: str | None = None,
    task_source: TaskSource | None = None,
    task_id: str | None = None,
    checkpointer: Any | None = None,
    queue_store: Any | None = None,
) -> CompiledStateGraph:
```

After the `orchestrator` node definition (line ~64), add:

```python
    if queue_store is not None:
        graph.add_node(
            "prioritizer",
            partial(prioritizer_node, app_cfg=app_cfg, repo_path=repo_path or ".", queue_store=queue_store),
        )
```

Change the entry edge. Replace:

```python
    graph.add_edge(START, "orchestrator")
```

with:

```python
    if queue_store is not None:
        graph.add_edge(START, "prioritizer")
        graph.add_edge("prioritizer", "orchestrator")
    else:
        graph.add_edge(START, "orchestrator")
```

Also pass `queue_store` through `run_workflow` and `run_workflow_interactive` signatures (add `queue_store: Any | None = None` param + forward to `build_graph`).

- [ ] **Step 2: Add queue_store to WorkflowRunner**

In `src/devflow/daemon/runner.py`, add `queue_store: QueueStore | None = None` to `__init__` and store it. In `run_task`, forward it to `run_workflow`/`run_workflow_interactive`:

```python
    def __init__(
        self,
        app_cfg: Config,
        event_bus: EventBus,
        locks: DaemonLocks,
        task_source: TaskSource | None = None,
        approval_bridge: ApprovalBridge | None = None,
        batch_store: BatchStore | None = None,
        queue_store: Any | None = None,
        on_task_change: Callable[[str | None], None] | None = None,
    ) -> None:
        # ... existing ...
        self._queue_store = queue_store
```

In `run_task`, change the `run_workflow_interactive`/`run_workflow` calls to pass `queue_store=self._queue_store`.

- [ ] **Step 3: Construct QueueStore in daemon main**

In `src/devflow/daemon/__main__.py`, after `batch_store` construction (~line 63), add:

```python
    from devflow.batch.queue_store import QueueStore
    queue_store = QueueStore(str(Path(repo_path) / ".devflow" / "queue.db"))
```

Pass `queue_store=queue_store` to the `WorkflowRunner(...)` constructor. Pass it to `create_app(..., queue_store=queue_store)`.

In the `finally` block at the end, add `queue_store.close()`.

- [ ] **Step 4: Run graph + runner tests to check for regressions**

Run: `python -m pytest tests/unit/test_graph.py tests/unit/daemon/test_runner.py -v`
Expected: PASS (graph tests may need updating if they assert entry edge — fix assertions)

- [ ] **Step 5: Commit**

```bash
git add src/devflow/graph.py src/devflow/daemon/runner.py src/devflow/daemon/__main__.py
git commit -m "feat(graph): wire prioritizer node before orchestrator + QueueStore in daemon"
```

---

### Task 5: Backend — `/api/queue/*` endpoints

**Files:**
- Modify: `src/devflow/daemon/web.py` — add queue endpoints + queue_store param
- Test: `tests/unit/daemon/test_web_queue.py`

**Interfaces:**
- Consumes: `QueueStore` (from `app.state.queue_store`), `EventBus` (for SSE publish)
- Produces: `GET /api/queue`, `PATCH /api/queue/reorder`, `POST /api/queue/move-up`, `POST /api/queue/move-down`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/daemon/test_web_queue.py`:

```python
"""Tests for /api/queue/* endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from devflow.batch.queue_store import QueueEntry, QueueStore
from devflow.config import Config, DaemonConfig, WorkflowConfig
from devflow.daemon.events import EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.daemon.web import create_app
from unittest.mock import MagicMock


def _make_app_with_queue(tmp_path: Path) -> tuple:
    store = QueueStore(str(tmp_path / "queue.db"))
    store.set_queue([
        QueueEntry(position=0, task_id="1", task_title="A", priority=0),
        QueueEntry(position=1, task_id="2", task_title="B", priority=1),
        QueueEntry(position=2, task_id="3", task_title="C", priority=3),
    ])
    cfg = Config(workflow=WorkflowConfig(task_source="mock"), providers={}, agents={})
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(
        cfg, locks, bus,
        runner=MagicMock(), scheduler=MagicMock(),
        queue_store=store,
    )
    return app, store


def test_get_queue_returns_ordered_list(tmp_path: Path) -> None:
    app, _ = _make_app_with_queue(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/queue")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    assert data[0]["task_id"] == "1"
    assert data[0]["position"] == 0


def test_reorder_moves_task(tmp_path: Path) -> None:
    app, _ = _make_app_with_queue(tmp_path)
    with TestClient(app) as client:
        resp = client.patch("/api/queue/reorder", json={"task_id": "3", "new_position": 0})
    assert resp.status_code == 200
    data = resp.json()
    assert [e["task_id"] for e in data] == ["3", "1", "2"]


def test_move_up(tmp_path: Path) -> None:
    app, _ = _make_app_with_queue(tmp_path)
    with TestClient(app) as client:
        resp = client.post("/api/queue/move-up", json={"task_id": "2"})
    assert resp.status_code == 200
    assert [e["task_id"] for e in resp.json()] == ["2", "1", "3"]


def test_move_down(tmp_path: Path) -> None:
    app, _ = _make_app_with_queue(tmp_path)
    with TestClient(app) as client:
        resp = client.post("/api/queue/move-down", json={"task_id": "1"})
    assert resp.status_code == 200
    assert [e["task_id"] for e in resp.json()] == ["2", "1", "3"]


def test_reorder_unknown_task_404(tmp_path: Path) -> None:
    app, _ = _make_app_with_queue(tmp_path)
    with TestClient(app) as client:
        resp = client.patch("/api/queue/reorder", json={"task_id": "bogus", "new_position": 0})
    assert resp.status_code == 404


def test_reorder_invalid_position_422(tmp_path: Path) -> None:
    app, _ = _make_app_with_queue(tmp_path)
    with TestClient(app) as client:
        resp = client.patch("/api/queue/reorder", json={"task_id": "1", "new_position": 99})
    assert resp.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_web_queue.py -v`
Expected: FAIL — 404 (endpoints don't exist)

- [ ] **Step 3: Add queue_store param to create_app + endpoints**

In `src/devflow/daemon/web.py`, add `queue_store: Any | None = None` to `create_app` signature. Add `app.state.queue_store = queue_store` in the state wiring block.

Add Pydantic models:

```python
class QueueReorderRequest(BaseModel):
    """Body of PATCH /api/queue/reorder."""

    task_id: str
    new_position: int


class QueueMoveRequest(BaseModel):
    """Body of POST /api/queue/move-up and move-down."""

    task_id: str
```

Add endpoints inside `create_app` (after the agents endpoints, before `/api/events`):

```python
    # -------------------------------------------------------------------
    # Control: execution queue
    # -------------------------------------------------------------------

    @app.get("/api/queue")
    async def get_queue() -> list[dict[str, Any]]:
        qs = app.state.queue_store  # type: ignore[attr-defined]
        if qs is None:
            return []
        return [e.model_dump() for e in qs.get_queue()]

    @app.patch("/api/queue/reorder")
    async def queue_reorder(req: QueueReorderRequest) -> list[dict[str, Any]]:
        qs = app.state.queue_store  # type: ignore[attr-defined]
        if qs is None:
            raise HTTPException(status_code=503, detail="No queue store configured")
        try:
            result = qs.reorder(req.task_id, req.new_position)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return [e.model_dump() for e in result]

    @app.post("/api/queue/move-up")
    async def queue_move_up(req: QueueMoveRequest) -> list[dict[str, Any]]:
        qs = app.state.queue_store  # type: ignore[attr-defined]
        if qs is None:
            raise HTTPException(status_code=503, detail="No queue store configured")
        try:
            result = qs.move_up(req.task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return [e.model_dump() for e in result]

    @app.post("/api/queue/move-down")
    async def queue_move_down(req: QueueMoveRequest) -> list[dict[str, Any]]:
        qs = app.state.queue_store  # type: ignore[attr-defined]
        if qs is None:
            raise HTTPException(status_code=503, detail="No queue store configured")
        try:
            result = qs.move_down(req.task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return [e.model_dump() for e in result]
```

Update `run_web_server` signature to forward `queue_store` to `create_app`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_web_queue.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/devflow/daemon/web.py tests/unit/daemon/test_web_queue.py
git commit -m "feat(daemon): /api/queue endpoints — GET, reorder, move-up, move-down"
```

---

### Task 6: Frontend — queue store + API client + types

**Files:**
- Create: `frontend/src/stores/queue.ts`
- Modify: `frontend/src/api/client.ts` — add queue functions
- Modify: `frontend/src/api/types.ts` — add QueueEntry

- [ ] **Step 1: Add types to `frontend/src/api/types.ts`**

Append:

```typescript
// --- Execution queue (Enhancements B) ---

export interface QueueEntry {
  position: number
  task_id: string
  task_title: string
  priority: number | null
  reason: string
  updated_at: string
}

export interface QueueReorderRequest {
  task_id: string
  new_position: number
}
```

- [ ] **Step 2: Add API functions to `frontend/src/api/client.ts`**

Add imports for `QueueEntry`, `QueueReorderRequest`, then append:

```typescript
// --- Control: execution queue (Enhancements B) ---
export const getQueue = () => getJson<QueueEntry[]>('/queue')
export const reorderQueue = (req: QueueReorderRequest) =>
  patchJson<QueueEntry[]>('/queue/reorder', req)
export const queueMoveUp = (taskId: string) =>
  postJson<QueueEntry[]>('/queue/move-up', { task_id: taskId })
export const queueMoveDown = (taskId: string) =>
  postJson<QueueEntry[]>('/queue/move-down', { task_id: taskId })
```

- [ ] **Step 3: Create `frontend/src/stores/queue.ts`**

```typescript
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getQueue, queueMoveDown, queueMoveUp, reorderQueue } from '@/api/client'
import type { QueueEntry } from '@/api/types'

export const useQueueStore = defineStore('queue', () => {
  const queue = ref<QueueEntry[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const lastUpdated = ref<Date | null>(null)

  async function fetch() {
    loading.value = true
    error.value = null
    try {
      queue.value = await getQueue()
      lastUpdated.value = new Date()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  async function reorder(taskId: string, newPosition: number) {
    error.value = null
    try {
      queue.value = await reorderQueue({ task_id: taskId, new_position: newPosition })
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    }
  }

  async function moveUp(taskId: string) {
    error.value = null
    try {
      queue.value = await queueMoveUp(taskId)
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    }
  }

  async function moveDown(taskId: string) {
    error.value = null
    try {
      queue.value = await queueMoveDown(taskId)
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    }
  }

  return { queue, loading, error, lastUpdated, fetch, reorder, moveUp, moveDown }
})
```

- [ ] **Step 4: Verify typecheck**

Run: `cd frontend && npm run typecheck`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/stores/queue.ts frontend/src/api/client.ts frontend/src/api/types.ts
git commit -m "feat(frontend): queue store + API client + types"
```

---

### Task 7: Frontend — QueuePanel with d&d + up/down buttons

**Files:**
- Create: `frontend/src/components/controls/QueuePanel.vue`
- Modify: `frontend/src/components/controls/RunTab.vue` — integrate QueuePanel
- Modify: `frontend/src/composables/useSSE.ts` — `queue.updated` listener
- Modify: `frontend/src/style.css` — queue panel + d&d styles

- [ ] **Step 1: Create `QueuePanel.vue`**

```vue
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useQueueStore } from '@/stores/queue'
import { usePolling } from '@/composables/usePolling'

const queue = useQueueStore()
const draggedTaskId = ref<string | null>(null)
const dragOverTaskId = ref<string | null>(null)

const { refresh } = usePolling(() => queue.fetch(), 30000)

function onDragStart(taskId: string) {
  draggedTaskId.value = taskId
}

function onDragOver(taskId: string, e: DragEvent) {
  e.preventDefault()
  dragOverTaskId.value = taskId
}

function onDrop(targetTaskId: string, e: DragEvent) {
  e.preventDefault()
  if (draggedTaskId.value && draggedTaskId.value !== targetTaskId) {
    const targetIdx = queue.queue.findIndex((q) => q.task_id === targetTaskId)
    if (targetIdx >= 0) {
      void queue.reorder(draggedTaskId.value, targetIdx)
    }
  }
  draggedTaskId.value = null
  dragOverTaskId.value = null
}

function priorityClass(p: number | null): string {
  if (p === 0) return 'prio-critical'
  if (p === 1) return 'prio-urgent'
  return 'prio-normal'
}
</script>

<template>
  <section class="queue-panel">
    <div class="tasks-header">
      <h3>Execution queue</h3>
      <div class="refresh-section">
        <button @click="refresh()" :disabled="queue.loading" class="refresh-btn">
          ↻ {{ queue.loading ? 'Загрузка…' : 'Обновить' }}
        </button>
        <small v-if="queue.lastUpdated" class="last-updated">
          Обновлено: {{ queue.lastUpdated.toLocaleTimeString() }}
        </small>
      </div>
    </div>
    <p v-if="queue.error" class="error">{{ queue.error }}</p>
    <p v-if="!queue.queue.length && !queue.loading">Очередь пуста. Запустите задачу — LLM оценит порядок.</p>
    <ul v-if="queue.queue.length" class="queue-list">
      <li
        v-for="entry in queue.queue"
        :key="entry.task_id"
        class="queue-item"
        :class="{ 'drag-over': dragOverTaskId === entry.task_id }"
        draggable="true"
        @dragstart="onDragStart(entry.task_id)"
        @dragover="onDragOver(entry.task_id, $event)"
        @drop="onDrop(entry.task_id, $event)"
        @dragend="draggedTaskId = null; dragOverTaskId = null"
      >
        <span class="drag-handle" title="Перетащите для изменения порядка">⠿</span>
        <span class="queue-position">{{ entry.position + 1 }}.</span>
        <code>#{{ entry.task_id }}</code>
        <span class="queue-title">{{ entry.task_title }}</span>
        <span v-if="entry.priority !== null" :class="priorityClass(entry.priority)">[r{{ entry.priority }}]</span>
        <span class="queue-actions">
          <button
            @click="queue.moveUp(entry.task_id)"
            :disabled="entry.position === 0"
            class="move-btn"
            title="Вверх"
          >↑</button>
          <button
            @click="queue.moveDown(entry.task_id)"
            :disabled="entry.position === queue.queue.length - 1"
            class="move-btn"
            title="Вниз"
          >↓</button>
        </span>
      </li>
    </ul>
    <small class="dnd-hint">Перетащите ⠿ для изменения порядка</small>
  </section>
</template>
```

- [ ] **Step 2: Integrate QueuePanel into RunTab.vue**

In `frontend/src/components/controls/RunTab.vue`, add import and place `<QueuePanel />` after the "Recent runs" section. Add at the top of `<script setup>`:

```typescript
import QueuePanel from '@/components/controls/QueuePanel.vue'
```

And in the template, after the recent runs `<ul>`/`<p>`, add:

```vue
    <QueuePanel />
```

- [ ] **Step 3: Add `queue.updated` listener to useSSE.ts**

In `frontend/src/composables/useSSE.ts`, add import:

```typescript
import { useQueueStore } from '@/stores/queue'
```

Inside `connect()`, add:

```typescript
  const queue = useQueueStore()
```

After the `tasks.updated` listener, add:

```typescript
    source.addEventListener('queue.updated', () => {
      void queue.fetch()
    })
```

- [ ] **Step 4: Add queue styles to `frontend/src/style.css`**

Append:

```css
/* --- Execution queue --- */
.queue-panel { margin-top: 1.5rem; }
.queue-list { list-style: none; padding: 0; margin: 0; }
.queue-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem;
  border: 1px solid #e1e1e3;
  border-radius: 4px;
  margin-bottom: 0.3rem;
  background: #fff;
  cursor: grab;
}
.queue-item.drag-over {
  border-color: #0366d6;
  background: #f0f7ff;
}
.queue-item:active { cursor: grabbing; }
.drag-handle { color: #999; font-size: 1.2rem; cursor: grab; }
.queue-position { font-weight: 600; min-width: 1.5rem; }
.queue-title { flex: 1; }
.queue-actions { display: flex; gap: 0.2rem; }
.move-btn {
  padding: 0.1rem 0.5rem;
  font-size: 0.9rem;
  margin-right: 0;
}
.dnd-hint { display: block; color: #999; font-size: 0.75rem; margin-top: 0.5rem; }
```

- [ ] **Step 5: Verify typecheck + build**

Run: `cd frontend && npm run typecheck && npm run build`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/controls/QueuePanel.vue frontend/src/components/controls/RunTab.vue frontend/src/composables/useSSE.ts frontend/src/style.css
git commit -m "feat(frontend): QueuePanel with native d&d + up/down buttons + queue.updated SSE"
```

---

### Task 8: Smoke test + final verification

**Files:** None (verification only)

- [ ] **Step 1: Run full backend test suite**

Run: `python -m pytest tests/ -q`
Expected: All tests PASS (existing + new queue/prioritizer tests)

- [ ] **Step 2: Build frontend**

Run: `cd frontend && npm run build`
Expected: PASS

- [ ] **Step 3: Restart daemon + Playwright smoke test**

Restart daemon. Navigate to `/controls` → Run tab. Verify:
- QueuePanel renders (may be empty if no prioritizer run yet)
- `GET /api/queue` responds

- [ ] **Step 4: Final commit if needed**

---

## Self-Review

**1. Spec coverage:**
- ✅ LLM prioritizer node (Section 1) → Tasks 2, 3, 4
- ✅ QueueStore SQLite (Section 2) → Task 1
- ✅ REST API (Section 3) → Task 5
- ✅ UI d&d + up/down (Section 4) → Task 7
- ✅ Auto re-evaluate (Section 1) → Task 4 (graph entry edge)
- ✅ SSE queue.updated (Section 4) → Tasks 3, 7
- ✅ Fallback (orchestrator uses select_next_todo) → Task 3

**2. Placeholder scan:** No TBD/TODO. All steps have code. ✅

**3. Type consistency:**
- `QueueEntry` fields (position, task_id, task_title, priority, reason, updated_at) consistent across Tasks 1, 5, 6, 7 ✅
- `QueueStore` methods (get_queue, set_queue, reorder, move_up, move_down, next_task_id) consistent across Tasks 1, 3, 5 ✅
- `prioritizer_node(state, *, app_cfg, repo_path, queue_store, event_bus)` consistent across Tasks 3, 4 ✅
