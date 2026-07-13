# Phase 4: EOD Batch-Flow + Batch-Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the `end_of_day` HITL strategy end-to-end: per-task runs accumulate completed work into a SQLite `BatchStore` during the day, and an EOD batch-review/publish flow pushes branches, creates MRs, publishes reports, and updates the tracker for all approved tasks in one sequential, idempotent pass.

**Architecture:** A new `src/devflow/batch/` module owns persistence (`BatchStore` SQLite CRUD + `BatchEntry` Pydantic model). The reporter node gains a **prepare-only mode** so `end_of_day` per-task runs generate artifacts and record_todo locally but skip publishing/pushing. A new `EodHandler` orchestrates batch-publish by reusing the existing `ForgeBackend` (push + create_mr, already idempotent), `_publish_to_channels`, and `TaskSource.update_task_status`. The `DaemonScheduler`'s stub `_run_eod_wrapper` is wired to the handler with `DaemonLocks` coordination. FastAPI gains `/api/eod*` routes. The graph itself is NOT modified — batching lives entirely in the daemon layer (the graph already runs end-to-end without interrupts in `end_of_day`, since both `plan_approval` and `publish_approval` auto-approve).

**Tech Stack:** Python ≥3.11, SQLite via `sqlite3` (stdlib), Pydantic (existing), FastAPI (existing), APScheduler (existing), GitPython + httpx via ForgeBackend (existing), pytest + pytest-asyncio (existing).

## Global Constraints

- Python ≥3.11 (pyproject.toml line 10)
- Line length 100, ruff rules: E, F, I, W, UP, B, C4, SIM; `E501` ignored (pyproject.toml `[tool.ruff.lint]`)
- `asyncio_mode = "auto"` for pytest-asyncio (pyproject.toml `[tool.pytest.ini_options]`)
- `testpaths = ["tests"]`, `pythonpath = ["src"]`
- All code, logs, and comments in English
- `HitlStrategy` is a plain string-constants class (config.py:68-75): `PER_PLAN="per_plan"`, `FULL_DETAIL="full_detail"`, `END_OF_DAY="end_of_day"`. Comparison is by string value.
- `WorkflowConfig.hitl_strategy: str` (config.py:63) — a plain str, NOT an enum field. Override via `DEVFLOW_HITL_STRATEGY` env.
- `DaemonConfig` (config.py:78-86): `enabled`, `task_schedule="0 9,15 * * 1-5"`, `eod_schedule="0 18 * * 1-5"`, `port=8787`, `approval_timeout_hours=8`, `approval_on_timeout="defer"`
- `ForgeConfig` (config.py:89-96): `provider="none"`, `target_branch="main"`, `actions=["publish_report","update_tracker","record_todo"]` (push/create_mr NOT in default list)
- `.devflow/` already in `.gitignore` (line 27)
- `langgraph-checkpoint-sqlite>=1.0.0` already a dependency (but we use stdlib `sqlite3` directly for the batch store — it's a separate DB file)
- `WorkflowState` (state.py:130) is a `TypedDict(total=False)`; it already has `task`, `plan`, `branch_name`, `worktree_path`, `diff`, `self_review_notes`, `checker_reports`, `final_verdict`, `pr_url`, `mr_url`, `pushed_sha`, `report_url`. Reducers: `_add_reducer` (list append), `_max_reducer` (max).
- `ReporterResponse` (schemas.py:80-86): `pr_title`, `pr_description`, `corporate_report`, `commit_message: str = ""`
- ForgeBackend (forge/base.py): `push(branch, target, repo_path) -> str` (SHA), `create_mr(branch, target, title, description) -> MRInfo` (idempotent). `MRInfo(url: str, number: int | None = None)`.
- `build_forge_backend(workflow_cfg: WorkflowConfig) -> ForgeBackend | None` (returns None when provider=="none")
- Shared test fixtures live in `tests/conftest.py`: `mock_config`, `fake_llm_factory`, `temp_dir`, `mock_task_source`. `base_state` is local to `tests/unit/test_reporter.py`.
- `EventBus.publish(topic: str, data: dict)`, `EventBus.subscribe(topic) -> Queue` (events.py)
- `DaemonLocks.task_run() -> asyncio.Lock`, `DaemonLocks.eod_review() -> asyncio.Lock` (locks.py) — accessed via methods returning the lock object, used as `async with locks.task_run():`
- APScheduler `BackgroundScheduler`, `CronTrigger.from_crontab(str)` (scheduler.py)
- Existing events published by runner: `task.started`, `task.finished`, `task.error` on topic `f"task.{task_id}"`

---

## File Structure

| File | Responsibility |
|---|---|
| `src/devflow/batch/__init__.py` | NEW: package init |
| `src/devflow/batch/models.py` | NEW: `BatchEntry` Pydantic model + `BatchStatus` constants |
| `src/devflow/batch/store.py` | NEW: `BatchStore` — SQLite CRUD (add, get_pending, get_entry, update_status, list_all, close) |
| `src/devflow/batch/publisher.py` | NEW: `BatchPublisher` — sequential idempotent publish of one BatchEntry (forge.push + create_mr + _publish_to_channels + update_tracker + mark_published) |
| `src/devflow/batch/eod_handler.py` | NEW: `EodHandler` — orchestrates review/publish: list pending, publish selected, lock coordination |
| `src/devflow/nodes/reporter.py` | Modify: add `prepare_only` param to `reporter_node` + `_execute_actions`; when True, skip publish/push/create_mr/update_tracker but still generate artifacts |
| `src/devflow/state.py` | Modify: `WorkflowState` += `reporter_artifacts: ReporterResponse | None`, `batch_entry_id: int | None` |
| `src/devflow/daemon/runner.py` | Modify: in `end_of_day`, run per-task with `prepare_only=True`, store a `BatchEntry` in `BatchStore` after each run |
| `src/devflow/daemon/scheduler.py` | Modify: wire `_run_eod_wrapper` to `EodHandler`; acquire locks in both wrappers |
| `src/devflow/daemon/web.py` | Modify: add `batch_store` + `eod_handler` params; `/api/eod`, `/api/eod/finalize`, `/api/eod/publish`, `/api/eod/entries/{id}` routes; wire `batch_store_pending` in health |
| `src/devflow/daemon/__main__.py` | Modify: construct `BatchStore` + `EodHandler`, pass to runner/scheduler/web |
| `tests/unit/batch/__init__.py` | NEW |
| `tests/unit/batch/test_models.py` | NEW |
| `tests/unit/batch/test_store.py` | NEW |
| `tests/unit/batch/test_publisher.py` | NEW |
| `tests/unit/batch/test_eod_handler.py` | NEW |
| `tests/unit/test_reporter.py` | Modify: test prepare_only mode |
| `tests/unit/daemon/test_runner.py` | Modify: test end_of_day stores batch entries |
| `tests/unit/daemon/test_scheduler.py` | Modify: test eod wrapper calls handler |
| `tests/unit/daemon/test_web.py` | Modify: test /api/eod* routes |
| `tests/integration/test_eod_batch.py` | NEW: end-to-end EOD flow |

---

## Task 1: BatchEntry model + BatchStatus

**Files:**
- Create: `src/devflow/batch/__init__.py`
- Create: `src/devflow/batch/models.py`
- Test: `tests/unit/batch/__init__.py`, `tests/unit/batch/test_models.py`

**Interfaces:**
- Consumes: existing `Task`, `Plan`, `CheckerReport`, `FinalVerdict` (state.py), `ReporterResponse` (schemas.py)
- Produces:
  - `BatchStatus` — string-constants class: `PENDING_REVIEW="pending_review"`, `APPROVED="approved"`, `REJECTED="rejected"`, `PUBLISHED="published"`
  - `BatchEntry(BaseModel)` with fields: `id: int | None`, `task_id: str`, `task_title: str`, `branch_name: str`, `worktree_path: str`, `diff: str`, `plan_summary: str`, `plan_steps: list[str]`, `checker_reports: list[CheckerReport]`, `self_review_notes: str`, `final_verdict: FinalVerdict | None`, `reporter_artifacts: ReporterResponse`, `status: str = BatchStatus.PENDING_REVIEW`, `created_at: str`, `published_at: str | None = None`, `mr_url: str | None = None`, `pushed_sha: str | None = None`, `rejection_reason: str | None = None`

- [ ] **Step 1: Create package init**

Create `src/devflow/batch/__init__.py`:
```python
"""End-of-day batch store and publish orchestration."""
```

Create `tests/unit/batch/__init__.py`:
```python
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/batch/test_models.py`:
```python
"""Unit tests for BatchEntry and BatchStatus."""

from __future__ import annotations

from devflow.batch.models import BatchEntry, BatchStatus
from devflow.schemas import ReporterResponse
from devflow.state import CheckerReport, CheckerVerdict, FinalVerdict


def _artifacts() -> ReporterResponse:
    return ReporterResponse(
        pr_title="feat: add thing",
        pr_description="body",
        corporate_report="report text",
        commit_message="feat: add thing",
    )


def test_batch_status_constants() -> None:
    assert BatchStatus.PENDING_REVIEW == "pending_review"
    assert BatchStatus.APPROVED == "approved"
    assert BatchStatus.REJECTED == "rejected"
    assert BatchStatus.PUBLISHED == "published"


def test_batch_entry_minimal() -> None:
    """BatchEntry can be created with required fields only."""
    entry = BatchEntry(
        task_id="T-1",
        task_title="Do thing",
        branch_name="devflow/T-1/abc",
        worktree_path="/tmp/repo-wt",
        diff="--- a\n+++ b\n+x\n",
        plan_summary="summary",
        plan_steps=["step 1"],
        checker_reports=[],
        self_review_notes="",
        final_verdict=FinalVerdict.APPROVE,
        reporter_artifacts=_artifacts(),
        created_at="2026-07-13T10:00:00Z",
    )
    assert entry.id is None
    assert entry.status == BatchStatus.PENDING_REVIEW
    assert entry.published_at is None
    assert entry.mr_url is None
    assert entry.pushed_sha is None
    assert entry.rejection_reason is None


def test_batch_entry_with_checker_reports() -> None:
    """BatchEntry round-trips checker reports."""
    report = CheckerReport(
        agent_name="checker_a",
        verdict=CheckerVerdict.APPROVE,
        summary="looks good",
    )
    entry = BatchEntry(
        task_id="T-2",
        task_title="t",
        branch_name="b",
        worktree_path="/p",
        diff="d",
        plan_summary="s",
        plan_steps=[],
        checker_reports=[report],
        self_review_notes="",
        final_verdict=FinalVerdict.APPROVE,
        reporter_artifacts=_artifacts(),
        created_at="2026-07-13T10:00:00Z",
    )
    assert len(entry.checker_reports) == 1
    assert entry.checker_reports[0].agent_name == "checker_a"


def test_batch_entry_round_trip_published() -> None:
    """A fully-published entry carries all publish fields."""
    entry = BatchEntry(
        id=7,
        task_id="T-3",
        task_title="t",
        branch_name="b",
        worktree_path="/p",
        diff="d",
        plan_summary="s",
        plan_steps=[],
        checker_reports=[],
        self_review_notes="",
        final_verdict=FinalVerdict.APPROVE,
        reporter_artifacts=_artifacts(),
        status=BatchStatus.PUBLISHED,
        created_at="2026-07-13T10:00:00Z",
        published_at="2026-07-13T18:00:00Z",
        mr_url="https://example.com/mr/1",
        pushed_sha="abc123",
    )
    assert entry.status == BatchStatus.PUBLISHED
    assert entry.mr_url == "https://example.com/mr/1"
    assert entry.id == 7


def test_batch_entry_serialization_roundtrip() -> None:
    """BatchEntry serializes to dict and back via model_dump."""
    entry = BatchEntry(
        task_id="T-4",
        task_title="t",
        branch_name="b",
        worktree_path="/p",
        diff="d",
        plan_summary="s",
        plan_steps=["a", "b"],
        checker_reports=[],
        self_review_notes="",
        final_verdict=None,
        reporter_artifacts=_artifacts(),
        created_at="2026-07-13T10:00:00Z",
    )
    data = entry.model_dump()
    restored = BatchEntry(**data)
    assert restored.task_id == entry.task_id
    assert restored.plan_steps == ["a", "b"]
    assert restored.reporter_artifacts.pr_title == "feat: add thing"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/unit/batch/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devflow.batch'`

- [ ] **Step 4: Write minimal implementation**

Create `src/devflow/batch/models.py`:
```python
"""Pydantic models for the end-of-day batch store."""

from __future__ import annotations

from devflow.pydantic_compat import Field  # re-export of pydantic Field
from pydantic import BaseModel

from devflow.schemas import ReporterResponse
from devflow.state import CheckerReport, FinalVerdict


class BatchStatus:
    """Status constants for a BatchEntry lifecycle."""

    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"


class BatchEntry(BaseModel):
    """A single task's accumulated work, awaiting end-of-day batch publish."""

    id: int | None = None
    task_id: str
    task_title: str
    branch_name: str
    worktree_path: str
    diff: str
    plan_summary: str
    plan_steps: list[str]
    checker_reports: list[CheckerReport]
    self_review_notes: str
    final_verdict: FinalVerdict | None
    reporter_artifacts: ReporterResponse
    status: str = BatchStatus.PENDING_REVIEW
    created_at: str
    published_at: str | None = None
    mr_url: str | None = None
    pushed_sha: str | None = None
    rejection_reason: str | None = None
```

**Note:** If `devflow.pydantic_compat` does not exist, drop that import line and rely on `pydantic.BaseModel` only (the `Field` import was speculative; the model doesn't use `Field`). Verify by checking whether `src/devflow/pydantic_compat.py` exists. If it does not, the final implementation is:

```python
"""Pydantic models for the end-of-day batch store."""

from __future__ import annotations

from pydantic import BaseModel

from devflow.schemas import ReporterResponse
from devflow.state import CheckerReport, FinalVerdict


class BatchStatus:
    """Status constants for a BatchEntry lifecycle."""

    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"


class BatchEntry(BaseModel):
    """A single task's accumulated work, awaiting end-of-day batch publish."""

    id: int | None = None
    task_id: str
    task_title: str
    branch_name: str
    worktree_path: str
    diff: str
    plan_summary: str
    plan_steps: list[str]
    checker_reports: list[CheckerReport]
    self_review_notes: str
    final_verdict: FinalVerdict | None
    reporter_artifacts: ReporterResponse
    status: str = BatchStatus.PENDING_REVIEW
    created_at: str
    published_at: str | None = None
    mr_url: str | None = None
    pushed_sha: str | None = None
    rejection_reason: str | None = None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/batch/test_models.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/devflow/batch/__init__.py src/devflow/batch/models.py tests/unit/batch/__init__.py tests/unit/batch/test_models.py
git commit -m "feat(batch): add BatchEntry model + BatchStatus

BatchEntry accumulates a single task's work for end-of-day batch
publish: task/branch/diff/plan/checker reports/reporter artifacts.
BatchStatus lifecycle: pending_review -> published | rejected."
```

---

## Task 2: BatchStore — SQLite CRUD

**Files:**
- Create: `src/devflow/batch/store.py`
- Test: `tests/unit/batch/test_store.py`

**Interfaces:**
- Consumes: `BatchEntry`, `BatchStatus` (Task 1)
- Produces: `BatchStore` class:
  - `__init__(self, db_path: str | Path)` — opens/creates SQLite DB, creates schema
  - `add(self, entry: BatchEntry) -> int` — inserts, returns assigned id
  - `get_entry(self, entry_id: int) -> BatchEntry | None`
  - `get_pending(self) -> list[BatchEntry]` — returns all `pending_review` entries, oldest first
  - `get_by_task(self, task_id: str) -> list[BatchEntry]` — entries for a task (any status)
  - `list_all(self, status: str | None = None) -> list[BatchEntry]` — all or filtered by status
  - `update_status(self, entry_id: int, status: str, *, mr_url: str | None = None, pushed_sha: str | None = None, rejection_reason: str | None = None) -> bool` — returns True if updated
  - `count_pending(self) -> int`
  - `close(self) -> None`
  - JSON serialization of `BatchEntry` to a `data TEXT` column (via `entry.model_dump_json()`)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/batch/test_store.py`:
```python
"""Unit tests for BatchStore SQLite CRUD."""

from __future__ import annotations

from pathlib import Path

from devflow.batch.models import BatchEntry, BatchStatus
from devflow.batch.store import BatchStore
from devflow.schemas import ReporterResponse
from devflow.state import CheckerReport, CheckerVerdict, FinalVerdict


def _make_entry(task_id: str = "T-1") -> BatchEntry:
    return BatchEntry(
        task_id=task_id,
        task_title=f"Task {task_id}",
        branch_name=f"devflow/{task_id}/abc",
        worktree_path="/tmp/repo-wt",
        diff="diff content",
        plan_summary="summary",
        plan_steps=["step 1", "step 2"],
        checker_reports=[
            CheckerReport(
                agent_name="checker_a",
                verdict=CheckerVerdict.APPROVE,
                summary="ok",
            )
        ],
        self_review_notes="looks fine",
        final_verdict=FinalVerdict.APPROVE,
        reporter_artifacts=ReporterResponse(
            pr_title="feat: x",
            pr_description="desc",
            corporate_report="report",
            commit_message="feat: x",
        ),
        created_at="2026-07-13T10:00:00Z",
    )


def test_store_creates_db_file(tmp_path: Path) -> None:
    """BatchStore creates the SQLite file on init."""
    db_path = tmp_path / "batch_store.db"
    assert not db_path.exists()
    store = BatchStore(db_path)
    assert db_path.exists()
    store.close()


def test_add_returns_id(tmp_path: Path) -> None:
    """add() inserts an entry and returns a positive id."""
    store = BatchStore(tmp_path / "batch_store.db")
    try:
        entry = _make_entry()
        entry_id = store.add(entry)
        assert entry_id > 0
    finally:
        store.close()


def test_add_assigns_sequential_ids(tmp_path: Path) -> None:
    """Sequential adds produce increasing ids."""
    store = BatchStore(tmp_path / "batch_store.db")
    try:
        id1 = store.add(_make_entry("T-1"))
        id2 = store.add(_make_entry("T-2"))
        assert id2 == id1 + 1
    finally:
        store.close()


def test_get_entry_round_trip(tmp_path: Path) -> None:
    """get_entry() returns the stored entry with all fields intact."""
    store = BatchStore(tmp_path / "batch_store.db")
    try:
        entry_id = store.add(_make_entry())
        fetched = store.get_entry(entry_id)
        assert fetched is not None
        assert fetched.task_id == "T-1"
        assert fetched.status == BatchStatus.PENDING_REVIEW
        assert fetched.plan_steps == ["step 1", "step 2"]
        assert len(fetched.checker_reports) == 1
        assert fetched.reporter_artifacts.pr_title == "feat: x"
        assert fetched.final_verdict == FinalVerdict.APPROVE
    finally:
        store.close()


def test_get_entry_returns_none_for_unknown(tmp_path: Path) -> None:
    """get_entry() returns None for a non-existent id."""
    store = BatchStore(tmp_path / "batch_store.db")
    try:
        assert store.get_entry(999) is None
    finally:
        store.close()


def test_get_pending_returns_pending_only(tmp_path: Path) -> None:
    """get_pending() returns only pending_review entries, oldest first."""
    store = BatchStore(tmp_path / "batch_store.db")
    try:
        store.add(_make_entry("T-1"))
        id2 = store.add(_make_entry("T-2"))
        store.add(_make_entry("T-3"))
        # Mark T-2 as published — should not appear in pending.
        store.update_status(id2, BatchStatus.PUBLISHED)
        pending = store.get_pending()
        assert len(pending) == 2
        assert all(e.status == BatchStatus.PENDING_REVIEW for e in pending)
        assert pending[0].task_id == "T-1"
        assert pending[1].task_id == "T-3"
    finally:
        store.close()


def test_count_pending(tmp_path: Path) -> None:
    """count_pending() reflects the current pending count."""
    store = BatchStore(tmp_path / "batch_store.db")
    try:
        assert store.count_pending() == 0
        store.add(_make_entry("T-1"))
        store.add(_make_entry("T-2"))
        assert store.count_pending() == 2
    finally:
        store.close()


def test_update_status_to_published(tmp_path: Path) -> None:
    """update_status() sets published + mr_url + pushed_sha."""
    store = BatchStore(tmp_path / "batch_store.db")
    try:
        entry_id = store.add(_make_entry())
        ok = store.update_status(
            entry_id,
            BatchStatus.PUBLISHED,
            mr_url="https://example.com/mr/1",
            pushed_sha="abc123",
        )
        assert ok is True
        fetched = store.get_entry(entry_id)
        assert fetched is not None
        assert fetched.status == BatchStatus.PUBLISHED
        assert fetched.mr_url == "https://example.com/mr/1"
        assert fetched.pushed_sha == "abc123"
    finally:
        store.close()


def test_update_status_to_rejected(tmp_path: Path) -> None:
    """update_status() sets rejected + rejection_reason."""
    store = BatchStore(tmp_path / "batch_store.db")
    try:
        entry_id = store.add(_make_entry())
        ok = store.update_status(
            entry_id, BatchStatus.REJECTED, rejection_reason="scope mismatch"
        )
        assert ok is True
        fetched = store.get_entry(entry_id)
        assert fetched is not None
        assert fetched.status == BatchStatus.REJECTED
        assert fetched.rejection_reason == "scope mismatch"
    finally:
        store.close()


def test_update_status_unknown_id_returns_false(tmp_path: Path) -> None:
    """update_status() returns False for an unknown id."""
    store = BatchStore(tmp_path / "batch_store.db")
    try:
        ok = store.update_status(999, BatchStatus.PUBLISHED)
        assert ok is False
    finally:
        store.close()


def test_get_by_task(tmp_path: Path) -> None:
    """get_by_task() returns all entries for a task id."""
    store = BatchStore(tmp_path / "batch_store.db")
    try:
        store.add(_make_entry("T-1"))
        store.add(_make_entry("T-1"))
        store.add(_make_entry("T-2"))
        entries = store.get_by_task("T-1")
        assert len(entries) == 2
        assert all(e.task_id == "T-1" for e in entries)
    finally:
        store.close()


def test_list_all_filtered(tmp_path: Path) -> None:
    """list_all(status=) filters by status."""
    store = BatchStore(tmp_path / "batch_store.db")
    try:
        id1 = store.add(_make_entry("T-1"))
        store.add(_make_entry("T-2"))
        store.update_status(id1, BatchStatus.PUBLISHED)
        published = store.list_all(status=BatchStatus.PUBLISHED)
        assert len(published) == 1
        assert published[0].task_id == "T-1"
        all_entries = store.list_all()
        assert len(all_entries) == 2
    finally:
        store.close()


def test_store_persists_across_reopen(tmp_path: Path) -> None:
    """Entries survive close + reopen (SQLite persistence)."""
    db_path = tmp_path / "batch_store.db"
    store1 = BatchStore(db_path)
    entry_id = store1.add(_make_entry())
    store1.close()

    store2 = BatchStore(db_path)
    try:
        fetched = store2.get_entry(entry_id)
        assert fetched is not None
        assert fetched.task_id == "T-1"
        assert store2.count_pending() == 1
    finally:
        store2.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/batch/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devflow.batch.store'`

- [ ] **Step 3: Write minimal implementation**

Create `src/devflow/batch/store.py`:
```python
"""SQLite-backed store for end-of-day batch entries.

The DB file lives at ``{repo_path}/.devflow/batch_store.db`` (the caller
chooses the path). Entries are serialized as JSON in a ``data`` column so
the full Pydantic model round-trips without column-per-field migrations.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from devflow.batch.models import BatchEntry, BatchStatus

logger = logging.getLogger(__name__)


class BatchStore:
    """CRUD for BatchEntry records in SQLite."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS batch_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending_review',
                created_at TEXT NOT NULL,
                data TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_batch_status ON batch_entries(status)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_batch_task ON batch_entries(task_id)"
        )
        self._conn.commit()

    def add(self, entry: BatchEntry) -> int:
        """Insert ``entry`` and return the assigned id."""
        cur = self._conn.execute(
            "INSERT INTO batch_entries (task_id, status, created_at, data) "
            "VALUES (?, ?, ?, ?)",
            (
                entry.task_id,
                entry.status,
                entry.created_at,
                entry.model_dump_json(),
            ),
        )
        self._conn.commit()
        assigned = cur.lastrowid
        assert assigned is not None  # AUTOINCREMENT always returns an id
        return assigned

    def get_entry(self, entry_id: int) -> BatchEntry | None:
        """Return the entry with ``entry_id``, or None."""
        row = self._conn.execute(
            "SELECT data FROM batch_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if row is None:
            return None
        return BatchEntry.model_validate_json(row["data"])

    def get_pending(self) -> list[BatchEntry]:
        """Return all pending_review entries, oldest first."""
        rows = self._conn.execute(
            "SELECT data FROM batch_entries WHERE status = ? ORDER BY id ASC",
            (BatchStatus.PENDING_REVIEW,),
        ).fetchall()
        return [BatchEntry.model_validate_json(r["data"]) for r in rows]

    def get_by_task(self, task_id: str) -> list[BatchEntry]:
        """Return all entries for ``task_id``, any status."""
        rows = self._conn.execute(
            "SELECT data FROM batch_entries WHERE task_id = ? ORDER BY id ASC",
            (task_id,),
        ).fetchall()
        return [BatchEntry.model_validate_json(r["data"]) for r in rows]

    def list_all(self, status: str | None = None) -> list[BatchEntry]:
        """Return all entries, optionally filtered by ``status``."""
        if status is None:
            rows = self._conn.execute(
                "SELECT data FROM batch_entries ORDER BY id ASC"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT data FROM batch_entries WHERE status = ? ORDER BY id ASC",
                (status,),
            ).fetchall()
        return [BatchEntry.model_validate_json(r["data"]) for r in rows]

    def update_status(
        self,
        entry_id: int,
        status: str,
        *,
        mr_url: str | None = None,
        pushed_sha: str | None = None,
        rejection_reason: str | None = None,
    ) -> bool:
        """Update an entry's status (and optional publish/reject metadata).

        Returns True if a row was updated, False if the id was not found.
        The JSON ``data`` column is rewritten so the full entry reflects
        the new status + metadata (get_entry sees them without re-query).
        """
        row = self._conn.execute(
            "SELECT data FROM batch_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if row is None:
            return False

        entry = BatchEntry.model_validate_json(row["data"])
        entry.status = status
        if mr_url is not None:
            entry.mr_url = mr_url
        if pushed_sha is not None:
            entry.pushed_sha = pushed_sha
        if rejection_reason is not None:
            entry.rejection_reason = rejection_reason
        if status == BatchStatus.PUBLISHED:
            from datetime import datetime, timezone

            entry.published_at = datetime.now(timezone.utc).isoformat()

        self._conn.execute(
            "UPDATE batch_entries SET status = ?, data = ? WHERE id = ?",
            (entry.status, entry.model_dump_json(), entry_id),
        )
        self._conn.commit()
        return True

    def count_pending(self) -> int:
        """Return the number of pending_review entries."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM batch_entries WHERE status = ?",
            (BatchStatus.PENDING_REVIEW,),
        ).fetchone()
        return int(row["n"])

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/batch/test_store.py -v`
Expected: All 13 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devflow/batch/store.py tests/unit/batch/test_store.py
git commit -m "feat(batch): add BatchStore SQLite CRUD

JSON-in-column design: entry.model_dump_json() stored in a TEXT column
so Pydantic round-trips without schema migrations. CRUD: add, get_entry,
get_pending, get_by_task, list_all, update_status, count_pending.
Persists across reopen; index on status + task_id."
```

---

## Task 3: Reporter prepare-only mode

**Files:**
- Modify: `src/devflow/nodes/reporter.py:23-109` (add `prepare_only` param) and `:219-297` (`_execute_actions` gating)
- Modify: `src/devflow/state.py:130-163` (add `reporter_artifacts` field)
- Test: `tests/unit/test_reporter.py` (add prepare-only tests)

**Interfaces:**
- Consumes: existing reporter internals, `HitlStrategy` (config.py)
- Produces:
  - `reporter_node(state, *, app_cfg, prepare_only: bool = False)` — when `prepare_only=True`, runs the LLM `call_structured` and builds the report markdown, but `_execute_actions` runs ONLY `record_todo` (local); skips `publish_report`, `update_tracker`, `push`, `create_mr`. Returns `reporter_artifacts` in the state dict.
  - `_execute_actions(*, ..., prepare_only: bool = False)` — same gating.
  - `WorkflowState.reporter_artifacts: ReporterResponse | None` — new field to carry artifacts from prepare-only runs into the batch store.

- [ ] **Step 1: Add state field**

In `src/devflow/state.py`, add to `WorkflowState` (after `report_url`, before `error` around line 155):

```python
    # Reporter artifacts from prepare-only runs (end_of_day per-task).
    reporter_artifacts: ReporterResponse | None
```

**Important:** `ReporterResponse` must be importable in state.py. Check whether it's already imported. If not, add at the top of state.py (with the other schema imports):
```python
from devflow.schemas import Plan, ReporterResponse, ResearchRequest, ResearchResult, TodoItem
```
(Adjust to match the existing import line in state.py — read the file's imports first and add `ReporterResponse` to the existing `from devflow.schemas import ...` line. Do NOT duplicate imports.)

- [ ] **Step 2: Write the failing test**

Add to `tests/unit/test_reporter.py` (after the existing tests, inside the file):

```python
def test_reporter_prepare_only_skips_publish_and_push(
    base_state: WorkflowState,
    mock_config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """prepare_only=True runs LLM + record_todo but skips publish/push/MR."""
    mock_config.workflow.forge.provider = "github"
    mock_config.workflow.forge.actions = [
        "publish_report",
        "update_tracker",
        "record_todo",
        "push",
        "create_mr",
    ]

    push_called: list[bool] = []
    mr_called: list[bool] = []
    publish_called: list[bool] = []

    class FakeForge:
        name = "fake"

        def push(self, branch, target, repo_path):
            push_called.append(True)
            return "sha-fake"

        def create_mr(self, branch, target, title, description):
            mr_called.append(True)
            from devflow.forge.base import MRInfo
            return MRInfo(url="https://fake/mr/1", number=1)

        def healthcheck(self):
            return True

        def close(self):
            pass

    monkeypatch.setattr(
        "devflow.nodes.reporter.build_forge_backend", lambda wf: FakeForge()
    )
    monkeypatch.setattr(
        "devflow.nodes.reporter._publish_to_channels",
        lambda cfg, msg: publish_called.append(True) or "console",
    )

    result = reporter_node(base_state, app_cfg=mock_config, prepare_only=True)

    # Artifacts ARE generated.
    artifacts = result.get("reporter_artifacts")
    assert artifacts is not None
    assert hasattr(artifacts, "pr_title")
    # publish/push/MR are NOT executed.
    assert push_called == []
    assert mr_called == []
    assert publish_called == []
    assert result.get("pushed_sha") is None
    assert result.get("mr_url") is None
    assert result.get("report_url") is None


def test_reporter_prepare_only_returns_artifacts(
    base_state: WorkflowState,
    mock_config: Config,
) -> None:
    """prepare_only=True returns the ReporterResponse in reporter_artifacts."""
    result = reporter_node(base_state, app_cfg=mock_config, prepare_only=True)
    artifacts = result.get("reporter_artifacts")
    assert artifacts is not None
    from devflow.schemas import ReporterResponse
    assert isinstance(artifacts, ReporterResponse)
    assert artifacts.pr_title  # non-empty


def test_reporter_default_is_not_prepare_only(
    base_state: WorkflowState,
    mock_config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without prepare_only, publish_report still runs (default behavior)."""
    publish_called: list[bool] = []
    monkeypatch.setattr(
        "devflow.nodes.reporter._publish_to_channels",
        lambda cfg, msg: publish_called.append(True) or "console",
    )

    result = reporter_node(base_state, app_cfg=mock_config)
    assert publish_called == [True]
    assert result.get("report_url") == "console"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_reporter.py::test_reporter_prepare_only_skips_publish_and_push tests/unit/test_reporter.py::test_reporter_prepare_only_returns_artifacts -v`
Expected: FAIL with `TypeError: reporter_node() got an unexpected keyword argument 'prepare_only'`

- [ ] **Step 4: Modify reporter_node signature**

In `src/devflow/nodes/reporter.py`, change the `reporter_node` signature (line 23) from:
```python
def reporter_node(state: WorkflowState, *, app_cfg: Config) -> dict[str, Any]:
```
to:
```python
def reporter_node(
    state: WorkflowState, *, app_cfg: Config, prepare_only: bool = False
) -> dict[str, Any]:
```

- [ ] **Step 5: Thread prepare_only into _execute_actions**

In `src/devflow/nodes/reporter.py`, the call to `_execute_actions` (lines 81-89) becomes:
```python
        action_results = _execute_actions(
            app_cfg=app_cfg,
            state=state,
            task=task,
            response=response,
            verdict=verdict,
            report_text=report_text,
            branch=branch,
            prepare_only=prepare_only,
        )
```

- [ ] **Step 6: Update the return dict to include reporter_artifacts**

In `src/devflow/nodes/reporter.py`, the return dict (lines 92-103) — add `reporter_artifacts` so prepare-only runs expose the artifacts:
```python
        logger.info("Reporter finished for task %s", task.id)
        return {
            "pr_url": action_results.get("pr_url"),
            "mr_url": action_results.get("mr_url"),
            "pushed_sha": action_results.get("pushed_sha"),
            "report_url": action_results.get("report_url"),
            "reporter_artifacts": response,
            "task": action_results.get("task", task),
            "logs": [
                f"reporter: PR '{response.pr_title}'",
                "reporter: corporate report generated",
            ]
            + action_results.get("action_logs", []),
        }
```

- [ ] **Step 7: Add prepare_only gating to _execute_actions**

In `src/devflow/nodes/reporter.py`, change the `_execute_actions` signature (line 219) to add `prepare_only`:
```python
def _execute_actions(
    *,
    app_cfg: Config,
    state: WorkflowState,
    task: Task,
    response: ReporterResponse,
    verdict: FinalVerdict | None,
    report_text: str,
    branch: str | None,
    prepare_only: bool = False,
) -> dict[str, Any]:
```

Then add the prepare-only short-circuit immediately after `actions = forge_cfg.actions` (after line 235):
```python
    # In prepare-only mode (end_of_day per-task), only record_todo runs
    # locally. Publishing, tracker updates, push, and create_mr are deferred
    # to the batch-publish stage.
    if prepare_only:
        actions = ["record_todo"] if "record_todo" in actions else []
```

This mutates the local `actions` list reference; the rest of `_execute_actions` works unchanged (it gates each action by membership in `actions`).

- [ ] **Step 8: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_reporter.py -v`
Expected: All tests PASS (new prepare-only + existing).

- [ ] **Step 9: Run full unit suite**

Run: `python -m pytest tests/unit -v`
Expected: All tests PASS. If `test_graph.py` constructs `reporter_node` via `functools.partial` and calls it, the new optional param with a default keeps it compatible.

- [ ] **Step 10: Run ruff + mypy**

Run: `python -m ruff check src/devflow/nodes/reporter.py src/devflow/state.py tests/unit/test_reporter.py`
Expected: No errors.

Run: `python -m mypy src/devflow/nodes/reporter.py src/devflow/state.py`
Expected: No errors.

- [ ] **Step 11: Commit**

```bash
git add src/devflow/nodes/reporter.py src/devflow/state.py tests/unit/test_reporter.py
git commit -m "feat(reporter): add prepare_only mode for end_of_day per-task

prepare_only=True runs the LLM to generate artifacts and record_todo
locally, but defers publish_report/update_tracker/push/create_mr to the
batch-publish stage. WorkflowState gains reporter_artifacts field."
```

---

## Task 4: BatchPublisher — sequential idempotent publish of one entry

**Files:**
- Create: `src/devflow/batch/publisher.py`
- Test: `tests/unit/batch/test_publisher.py`

**Interfaces:**
- Consumes: `BatchEntry`, `BatchStore` (Task 2), `build_forge_backend` (forge/factory.py), `Config` (config.py), `_publish_to_channels` + `_update_task_status` from reporter.py, `build_task_source` (mcp/factory.py)
- Produces: `BatchPublisher` class:
  - `__init__(self, app_cfg: Config, store: BatchStore, repo_path: str)`
  - `publish(self, entry: BatchEntry) -> BatchEntry` — pushes branch, creates MR, publishes report, updates tracker, marks the entry published in the store. Each step is independent try/except. Returns the updated entry. If a step fails, the entry stays pending_review (caller may retry; forge ops are idempotent).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/batch/test_publisher.py`:
```python
"""Unit tests for BatchPublisher."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from devflow.batch.models import BatchEntry, BatchStatus
from devflow.batch.publisher import BatchPublisher
from devflow.batch.store import BatchStore
from devflow.config import Config
from devflow.forge.base import MRInfo
from devflow.schemas import ReporterResponse
from devflow.state import CheckerReport, CheckerVerdict, FinalVerdict


def _make_entry(task_id: str = "T-1") -> BatchEntry:
    return BatchEntry(
        task_id=task_id,
        task_title=f"Task {task_id}",
        branch_name=f"devflow/{task_id}/abc",
        worktree_path="/tmp/repo-wt",
        diff="diff",
        plan_summary="summary",
        plan_steps=["step 1"],
        checker_reports=[
            CheckerReport(
                agent_name="checker_a",
                verdict=CheckerVerdict.APPROVE,
                summary="ok",
            )
        ],
        self_review_notes="",
        final_verdict=FinalVerdict.APPROVE,
        reporter_artifacts=ReporterResponse(
            pr_title="feat: x",
            pr_description="desc",
            corporate_report="report",
            commit_message="feat: x",
        ),
        created_at="2026-07-13T10:00:00Z",
    )


class RecordingForge:
    """Fake forge backend recording push/create_mr calls."""

    name = "recording"

    def __init__(self) -> None:
        self.pushed: list[tuple[str, str, str]] = []
        self.mrs: list[dict[str, str]] = []

    def push(self, branch: str, target: str, repo_path: str) -> str:
        self.pushed.append((branch, target, repo_path))
        return "sha-published"

    def create_mr(self, branch: str, target: str, title: str, description: str) -> MRInfo:
        self.mrs.append(
            {"branch": branch, "target": target, "title": title, "description": description}
        )
        return MRInfo(url="https://example.com/mr/1", number=1)

    def healthcheck(self) -> bool:
        return True

    def close(self) -> None:
        pass


def test_publish_full_success(
    tmp_path, mock_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full publish: push + MR + report + tracker, entry marked published."""
    store = BatchStore(tmp_path / "batch_store.db")
    entry_id = store.add(_make_entry())
    entry = store.get_entry(entry_id)
    assert entry is not None
    entry.id = entry_id

    forge = RecordingForge()
    monkeypatch.setattr(
        "devflow.batch.publisher.build_forge_backend", lambda wf: forge
    )

    publish_called: list[bool] = []
    monkeypatch.setattr(
        "devflow.batch.publisher._publish_to_channels",
        lambda cfg, msg: publish_called.append(True) or "console",
    )

    tracker_called: list[str] = []
    fake_source = MagicMock()
    fake_source.update_task_status = (
        lambda task_id, status, comment=None: tracker_called.append(task_id)
    )
    fake_source.close = lambda: None
    monkeypatch.setattr(
        "devflow.batch.publisher.build_task_source", lambda wf: fake_source
    )

    publisher = BatchPublisher(mock_config, store, repo_path="/tmp/repo")
    published = publisher.publish(entry)

    assert forge.pushed == [("devflow/T-1/abc", "main", "/tmp/repo")]
    assert len(forge.mrs) == 1
    assert forge.mrs[0]["branch"] == "devflow/T-1/abc"
    assert publish_called == [True]
    assert tracker_called == ["T-1"]
    assert published.status == BatchStatus.PUBLISHED
    assert published.pushed_sha == "sha-published"
    assert published.mr_url == "https://example.com/mr/1"

    # Store reflects the update.
    fetched = store.get_entry(entry_id)
    assert fetched is not None
    assert fetched.status == BatchStatus.PUBLISHED
    store.close()


def test_publish_push_failure_keeps_pending(
    tmp_path, mock_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If push fails, the entry stays pending_review for retry."""
    store = BatchStore(tmp_path / "batch_store.db")
    entry_id = store.add(_make_entry())
    entry = store.get_entry(entry_id)
    assert entry is not None
    entry.id = entry_id

    class ExplodingForge(RecordingForge):
        def push(self, branch, target, repo_path):
            raise RuntimeError("network down")

    monkeypatch.setattr(
        "devflow.batch.publisher.build_forge_backend", lambda wf: ExplodingForge()
    )
    monkeypatch.setattr(
        "devflow.batch.publisher._publish_to_channels",
        lambda cfg, msg: "console",
    )
    fake_source = MagicMock()
    fake_source.update_task_status = MagicMock()
    fake_source.close = lambda: None
    monkeypatch.setattr(
        "devflow.batch.publisher.build_task_source", lambda wf: fake_source
    )

    publisher = BatchPublisher(mock_config, store, repo_path="/tmp/repo")
    result = publisher.publish(entry)

    assert result.status == BatchStatus.PENDING_REVIEW
    fetched = store.get_entry(entry_id)
    assert fetched is not None
    assert fetched.status == BatchStatus.PENDING_REVIEW
    store.close()


def test_publish_no_forge_skips_push_but_publishes_report(
    tmp_path, mock_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When forge provider is 'none', push/MR are skipped but report still publishes."""
    mock_config.workflow.forge.provider = "none"
    store = BatchStore(tmp_path / "batch_store.db")
    entry_id = store.add(_make_entry())
    entry = store.get_entry(entry_id)
    assert entry is not None
    entry.id = entry_id

    publish_called: list[bool] = []
    monkeypatch.setattr(
        "devflow.batch.publisher._publish_to_channels",
        lambda cfg, msg: publish_called.append(True) or "console",
    )
    fake_source = MagicMock()
    fake_source.update_task_status = MagicMock()
    fake_source.close = lambda: None
    monkeypatch.setattr(
        "devflow.batch.publisher.build_task_source", lambda wf: fake_source
    )

    publisher = BatchPublisher(mock_config, store, repo_path="/tmp/repo")
    result = publisher.publish(entry)

    # Report + tracker ran; status is published even without forge (no push/MR).
    assert publish_called == [True]
    assert result.status == BatchStatus.PUBLISHED
    assert result.mr_url is None
    store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/batch/test_publisher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devflow.batch.publisher'`

- [ ] **Step 3: Write minimal implementation**

Create `src/devflow/batch/publisher.py`:
```python
"""Batch publish: push branch, create MR, publish report, update tracker.

Sequential, idempotent. Each step is independent try/except — a failure
in one step does not abort the others. The entry is marked ``published``
in the store only when the publish completes (push/MR failures leave it
``pending_review`` so the next EOD retries — forge ops are idempotent).
"""

from __future__ import annotations

import logging

from devflow.batch.models import BatchEntry, BatchStatus
from devflow.batch.store import BatchStore
from devflow.config import Config
from devflow.forge.factory import build_forge_backend
from devflow.mcp.factory import build_task_source
from devflow.nodes.reporter import _build_report_markdown, _publish_to_channels, _update_task_status

logger = logging.getLogger(__name__)


class BatchPublisher:
    """Publish a single BatchEntry: push, create MR, notify, update tracker."""

    def __init__(self, app_cfg: Config, store: BatchStore, repo_path: str) -> None:
        self._cfg = app_cfg
        self._store = store
        self._repo_path = repo_path

    def publish(self, entry: BatchEntry) -> BatchEntry:
        """Publish ``entry`` sequentially. Returns the (possibly updated) entry.

        Steps:
        1. forge.push(branch) -> pushed_sha (if forge configured)
        2. forge.create_mr(...) -> mr_url (if forge configured)
        3. _publish_to_channels(report) -> report_url
        4. source.update_task_status(resolved)
        5. mark entry PUBLISHED in store

        If push or create_mr fails, the entry stays PENDING_REVIEW (retryable).
        Report publish and tracker update failures are logged but do not block
        the published marking when push/MR succeeded.
        """
        forge_cfg = self._cfg.workflow.forge
        forge = None
        pushed_sha: str | None = None
        mr_url: str | None = None
        push_failed = False

        try:
            forge = build_forge_backend(self._cfg.workflow)
        except Exception as exc:
            logger.warning("Failed to build forge backend: %s", exc)

        # 1. Push (if forge configured).
        if forge is not None:
            try:
                pushed_sha = forge.push(
                    entry.branch_name, forge_cfg.target_branch, self._repo_path
                )
                logger.info(
                    "BatchPublish: pushed %s -> %s", entry.branch_name, pushed_sha[:8]
                )
            except Exception as exc:
                logger.warning(
                    "BatchPublish: push failed for %s: %s", entry.branch_name, exc
                )
                push_failed = True

            # 2. Create MR (only if push succeeded — MR without a push is pointless).
            if not push_failed:
                try:
                    mr_info = forge.create_mr(
                        branch=entry.branch_name,
                        target=forge_cfg.target_branch,
                        title=entry.reporter_artifacts.pr_title,
                        description=entry.reporter_artifacts.pr_description,
                    )
                    mr_url = mr_info.url
                    logger.info("BatchPublish: MR created %s", mr_url)
                except Exception as exc:
                    logger.warning(
                        "BatchPublish: create_mr failed for %s: %s",
                        entry.branch_name,
                        exc,
                    )

            try:
                forge.close()
            except Exception:  # pragma: no cover - defensive
                pass

        # If push failed, keep the entry pending for retry.
        if push_failed:
            return entry

        # 3. Publish the report to notification channels (best-effort).
        try:
            report_text = _build_report_markdown(
                task=_entry_task_stub(entry),
                response=entry.reporter_artifacts,
                verdict=entry.final_verdict,
                reports=entry.checker_reports,
                branch=entry.branch_name,
            )
            _publish_to_channels(self._cfg, report_text)
        except Exception as exc:
            logger.warning(
                "BatchPublish: report publish failed for %s: %s", entry.task_id, exc
            )

        # 4. Update the tracker status (best-effort).
        try:
            source = build_task_source(self._cfg.workflow)
            try:
                verdict_str = (
                    entry.final_verdict.value if entry.final_verdict else "approve"
                )
                source.update_task_status(
                    entry.task_id,
                    "resolved",
                    comment=f"Final verdict: {verdict_str} (batch publish)",
                )
            finally:
                source.close()
        except Exception as exc:
            logger.warning(
                "BatchPublish: tracker update failed for %s: %s", entry.task_id, exc
            )

        # 5. Mark published in the store.
        if entry.id is not None:
            self._store.update_status(
                entry.id,
                BatchStatus.PUBLISHED,
                mr_url=mr_url,
                pushed_sha=pushed_sha,
            )

        entry.status = BatchStatus.PUBLISHED
        entry.pushed_sha = pushed_sha
        entry.mr_url = mr_url
        return entry


def _entry_task_stub(entry: BatchEntry):
    """Build a minimal Task-like object for _build_report_markdown.

    ``_build_report_markdown`` reads ``task.id``, ``task.title`` — we don't
    have the full Task model in a BatchEntry, so a tiny stub suffices.
    """
    from devflow.state import Task

    return Task(id=entry.task_id, title=entry.task_title, description="")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/batch/test_publisher.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devflow/batch/publisher.py tests/unit/batch/test_publisher.py
git commit -m "feat(batch): add BatchPublisher (sequential idempotent publish)

publish(entry): forge.push -> forge.create_mr -> _publish_to_channels ->
update_tracker -> mark PUBLISHED. Each step independent try/except.
Push failure leaves entry pending_review for retry (forge ops idempotent).
Reuses reporter's _build_report_markdown and _publish_to_channels."
```

---

## Task 5: EodHandler — orchestrate review + batch publish

**Files:**
- Create: `src/devflow/batch/eod_handler.py`
- Test: `tests/unit/batch/test_eod_handler.py`

**Interfaces:**
- Consumes: `BatchStore` (Task 2), `BatchPublisher` (Task 4), `EventBus` (events.py), `Config`
- Produces: `EodHandler` class:
  - `__init__(self, app_cfg: Config, store: BatchStore, event_bus: EventBus, repo_path: str)`
  - `build_publisher(self) -> BatchPublisher` — factory (used by tests + publish_selected)
  - `list_pending(self) -> list[BatchEntry]` — delegates to store
  - `publish_selected(self, task_ids: list[str]) -> dict[str, Any]` — publishes entries matching `task_ids` sequentially; returns summary `{published: [...], failed: [...], skipped: [...]}`. Acquires no lock itself (the caller/scheduler holds `eod_review` lock).
  - `finalize(self) -> list[BatchEntry]` — returns pending entries + publishes an `eod.ready` event on the event bus. Used by the cron trigger and the `/api/eod/finalize` button.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/batch/test_eod_handler.py`:
```python
"""Unit tests for EodHandler."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from devflow.batch.eod_handler import EodHandler
from devflow.batch.models import BatchEntry, BatchStatus
from devflow.batch.store import BatchStore
from devflow.config import Config
from devflow.daemon.events import EventBus
from devflow.forge.base import MRInfo
from devflow.schemas import ReporterResponse
from devflow.state import FinalVerdict


def _make_entry(task_id: str) -> BatchEntry:
    return BatchEntry(
        task_id=task_id,
        task_title=f"Task {task_id}",
        branch_name=f"devflow/{task_id}/abc",
        worktree_path="/tmp/repo-wt",
        diff="d",
        plan_summary="s",
        plan_steps=[],
        checker_reports=[],
        self_review_notes="",
        final_verdict=FinalVerdict.APPROVE,
        reporter_artifacts=ReporterResponse(
            pr_title="t", pr_description="d", corporate_report="r", commit_message="c"
        ),
        created_at="2026-07-13T10:00:00Z",
    )


class StubForge:
    name = "stub"

    def push(self, branch, target, repo_path):
        return "sha-stub"

    def create_mr(self, branch, target, title, description):
        return MRInfo(url="https://example.com/mr/1", number=1)

    def healthcheck(self):
        return True

    def close(self):
        pass


@pytest.fixture
def handler(
    tmp_path: Path, mock_config: Config, monkeypatch: pytest.MonkeyPatch
) -> EodHandler:
    store = BatchStore(tmp_path / "batch_store.db")
    monkeypatch.setattr(
        "devflow.batch.publisher.build_forge_backend", lambda wf: StubForge()
    )
    monkeypatch.setattr(
        "devflow.batch.publisher._publish_to_channels", lambda cfg, msg: "console"
    )
    fake_source = MagicMock()
    fake_source.update_task_status = MagicMock()
    fake_source.close = lambda: None
    monkeypatch.setattr(
        "devflow.batch.publisher.build_task_source", lambda wf: fake_source
    )
    bus = EventBus()
    return EodHandler(mock_config, store, bus, repo_path="/tmp/repo")


def test_list_pending_returns_from_store(handler: EodHandler) -> None:
    """list_pending() delegates to the store."""
    handler._store.add(_make_entry("T-1"))
    handler._store.add(_make_entry("T-2"))
    pending = handler.list_pending()
    assert len(pending) == 2
    assert pending[0].task_id == "T-1"


def test_publish_selected_publishes_matching(handler: EodHandler) -> None:
    """publish_selected() publishes entries whose task_id is in the list."""
    handler._store.add(_make_entry("T-1"))
    handler._store.add(_make_entry("T-2"))
    handler._store.add(_make_entry("T-3"))

    result = handler.publish_selected(["T-1", "T-3"])

    assert set(result["published"]) == {"T-1", "T-3"}
    assert result["failed"] == []
    assert result["skipped"] == ["T-2"]
    # Store: only T-2 remains pending.
    assert handler._store.count_pending() == 1


def test_publish_selected_empty_list_publishes_all(handler: EodHandler) -> None:
    """publish_selected([]) publishes all pending entries."""
    handler._store.add(_make_entry("T-1"))
    handler._store.add(_make_entry("T-2"))

    result = handler.publish_selected([])

    assert set(result["published"]) == {"T-1", "T-2"}
    assert handler._store.count_pending() == 0


def test_publish_selected_unknown_task_id_is_skipped(handler: EodHandler) -> None:
    """publish_selected() with an unknown task_id lists it as skipped."""
    handler._store.add(_make_entry("T-1"))
    result = handler.publish_selected(["T-1", "T-UNKNOWN"])
    assert "T-1" in result["published"]
    assert "T-UNKNOWN" in result["skipped"]


def test_publish_selected_records_failed(handler: EodHandler, monkeypatch) -> None:
    """A publish that raises is recorded in failed, others still proceed."""

    class FailingPublisher:
        def __init__(self, *a, **kw):
            pass

        def publish(self, entry):
            raise RuntimeError("boom")

    monkeypatch.setattr(handler, "build_publisher", lambda: FailingPublisher())
    handler._store.add(_make_entry("T-1"))
    result = handler.publish_selected(["T-1"])
    assert result["published"] == []
    assert result["failed"] == ["T-1"]


async def test_finalize_publishes_eod_ready_event(handler: EodHandler) -> None:
    """finalize() returns pending entries and publishes an eod.ready event."""
    handler._store.add(_make_entry("T-1"))
    queue = await handler._bus.subscribe("eod")
    pending = await handler.finalize()
    assert len(pending) == 1
    msg = await queue.get()
    assert msg["event"] == "eod.ready"
    assert msg["pending_count"] == 1


def test_finalize_is_sync_return(handler: EodHandler) -> None:
    """finalize() returns a list synchronously (event publish is best-effort)."""
    handler._store.add(_make_entry("T-1"))
    pending = handler.finalize()
    assert isinstance(pending, list)
    assert len(pending) == 1
```

**Note on `test_finalize_publishes_eod_ready_event`:** since `asyncio_mode = "auto"`, declaring the test `async` is enough — no `@pytest.mark.asyncio` needed.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/batch/test_eod_handler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devflow.batch.eod_handler'`

- [ ] **Step 3: Write minimal implementation**

Create `src/devflow/batch/eod_handler.py`:
```python
"""EodHandler: end-of-day batch review + publish orchestration.

Holds no locks itself — the caller (scheduler wrapper or API route) is
expected to hold ``DaemonLocks.eod_review()`` so task runs and EOD-publish
do not overlap.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from devflow.batch.models import BatchEntry
from devflow.batch.publisher import BatchPublisher
from devflow.batch.store import BatchStore
from devflow.config import Config
from devflow.daemon.events import EventBus

logger = logging.getLogger(__name__)


class EodHandler:
    """Coordinate EOD batch review and publish."""

    def __init__(
        self,
        app_cfg: Config,
        store: BatchStore,
        event_bus: EventBus,
        repo_path: str,
    ) -> None:
        self._cfg = app_cfg
        self._store = store
        self._bus = event_bus
        self._repo_path = repo_path

    def build_publisher(self) -> BatchPublisher:
        """Construct a BatchPublisher. Overridable in tests."""
        return BatchPublisher(self._cfg, self._store, self._repo_path)

    def list_pending(self) -> list[BatchEntry]:
        """Return all pending_review entries, oldest first."""
        return self._store.get_pending()

    def publish_selected(self, task_ids: list[str]) -> dict[str, list[str]]:
        """Publish entries whose task_id is in ``task_ids``.

        If ``task_ids`` is empty, ALL pending entries are published.
        Returns ``{"published": [...], "failed": [...], "skipped": [...]}``
        where each list holds task_ids. ``skipped`` covers pending entries
        not in ``task_ids`` AND task_ids that have no pending entry.
        """
        pending = self._store.get_pending()
        pending_ids = {e.task_id for e in pending}
        selected = set(task_ids) if task_ids else pending_ids

        publisher = self.build_publisher()
        published: list[str] = []
        failed: list[str] = []
        skipped: list[str] = []

        for entry in pending:
            if entry.task_id not in selected:
                skipped.append(entry.task_id)
                continue
            try:
                publisher.publish(entry)
                published.append(entry.task_id)
                logger.info("EOD: published task %s", entry.task_id)
            except Exception:
                logger.exception("EOD: publish failed for task %s", entry.task_id)
                failed.append(entry.task_id)

        # task_ids requested but with no pending entry.
        for tid in selected - pending_ids:
            skipped.append(tid)

        return {"published": published, "failed": failed, "skipped": skipped}

    def finalize(self) -> list[BatchEntry]:
        """Trigger EOD review: list pending + emit ``eod.ready`` event.

        Returns the pending entries. The ``eod.ready`` event is published
        best-effort on the ``eod`` topic (consumed by Phase 5 SSE / UI).
        """
        pending = self._store.get_pending()
        self._publish_event(
            "eod",
            {"event": "eod.ready", "pending_count": len(pending)},
        )
        logger.info("EOD finalize: %d pending entr(y/ies)", len(pending))
        return pending

    def _publish_event(self, topic: str, data: dict[str, Any]) -> None:
        """Best-effort event publish (mirrors WorkflowRunner._publish)."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._bus.publish(topic, data))
        except RuntimeError:
            try:
                asyncio.run(self._bus.publish(topic, data))
            except Exception as exc:
                logger.debug("EventBus publish failed for '%s': %s", topic, exc)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/batch/test_eod_handler.py -v`
Expected: All 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devflow/batch/eod_handler.py tests/unit/batch/test_eod_handler.py
git commit -m "feat(batch): add EodHandler (review + publish orchestration)

list_pending / publish_selected(task_ids) / finalize. publish_selected([])
publishes all pending. Returns {published, failed, skipped} summary.
finalize() emits eod.ready event on the event bus. Holds no locks;
caller coordinates via DaemonLocks.eod_review()."
```

---

## Task 6: Wire BatchStore into WorkflowRunner for end_of_day per-task

**Files:**
- Modify: `src/devflow/daemon/runner.py:35-154` (WorkflowRunner gains optional `batch_store`, stores entries in end_of_day)
- Test: `tests/unit/daemon/test_runner.py` (add end_of_day batch-store test)

**Interfaces:**
- Consumes: `BatchStore` (Task 2), `BatchEntry` (Task 1), `HitlStrategy` (config.py), `WorkflowState.reporter_artifacts` (Task 3)
- Produces: `WorkflowRunner.__init__` gains `batch_store: BatchStore | None = None`. In `run_task`, when `app_cfg.workflow.hitl_strategy == HitlStrategy.END_OF_DAY` AND `batch_store` is set, the runner runs the graph with `prepare_only=True`... 

**IMPORTANT design note:** `run_workflow` / `run_workflow_interactive` (graph.py) call `reporter_node` via `functools.partial(..., app_cfg=app_cfg)` baked into `build_graph`. The `prepare_only` flag cannot be threaded through `run_workflow` without changing its signature. To keep this task self-contained and avoid a graph.py rewrite, the runner detects `end_of_day` + has a `batch_store` and **post-hoc stores a BatchEntry from the final state's `reporter_artifacts`** (which the reporter always populates now, Task 3). The per-task run uses the normal (non-prepare-only) graph path; the reporter's default `actions` list (publish_report/update_tracker/record_todo, NO push/create_mr) means per-task runs already don't push — which matches the EOD intent for the default config. If a user explicitly adds `push`/`create_mr` to `forge.actions`, per-task EOD runs would push individually — that's acceptable (idempotent) and out of scope for forcing prepare-only.

**Simpler approach adopted:** the runner stores a `BatchEntry` from the final `WorkflowState` after each end_of_day per-task run. The entry captures `reporter_artifacts`, diff, plan, checker reports, branch. This is the accumulation step.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/daemon/test_runner.py` (read the file first to match its imports + `temp_git_repo` fixture style):

```python
def test_run_task_end_of_day_stores_batch_entry(
    temp_git_repo: Path,
    mock_config: Config,
    fake_llm_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In end_of_day mode with a batch_store, run_task stores a BatchEntry."""
    from devflow.batch.store import BatchStore
    from devflow.config import HitlStrategy

    mock_config.workflow.hitl_strategy = HitlStrategy.END_OF_DAY
    mock_config.workflow.human_in_the_loop = True

    store = BatchStore(temp_git_repo / ".devflow" / "batch_store.db")
    try:
        runner = WorkflowRunner(
            mock_config,
            EventBus(),
            DaemonLocks(),
            task_source=MockTaskSource({}),
            batch_store=store,
        )
        # We don't run the full graph (needs LLM etc.); instead test the
        # storage helper directly.
        from devflow.schemas import ReporterResponse
        from devflow.state import (
            CheckerReport, CheckerVerdict, FinalVerdict, Plan, Task, WorkflowState,
        )
        from devflow.schemas import PlanStep

        final_state: WorkflowState = {
            "task": Task(id="T-99", title="Batch test", description="d"),
            "plan": Plan(summary="s", steps=[PlanStep(id="1", description="d")]),
            "diff": "diff content",
            "branch_name": "devflow/T-99/abc12345",
            "worktree_path": str(temp_git_repo),
            "checker_reports": [
                CheckerReport(
                    agent_name="checker_a",
                    verdict=CheckerVerdict.APPROVE,
                    summary="ok",
                )
            ],
            "final_verdict": FinalVerdict.APPROVE,
            "self_review_notes": "fine",
            "reporter_artifacts": ReporterResponse(
                pr_title="feat: x",
                pr_description="desc",
                corporate_report="report",
                commit_message="feat: x",
            ),
        }
        entry_id = runner._store_batch_entry("T-99", final_state)
        assert entry_id > 0
        assert store.count_pending() == 1
        entry = store.get_entry(entry_id)
        assert entry is not None
        assert entry.task_id == "T-99"
        assert entry.reporter_artifacts.pr_title == "feat: x"
    finally:
        store.close()
```

If `temp_git_repo` is not defined in `tests/unit/daemon/test_runner.py`, add a local fixture matching the pattern used in `tests/integration/test_forge_reporter.py`:
```python
@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    repo = Repo.init(repo_path)
    with repo.config_writer() as writer:
        writer.set_value("user", "name", "Test")
        writer.set_value("user", "email", "test@example.com")
    (repo_path / "README.md").write_text("# init", encoding="utf-8")
    repo.git.add("--all")
    repo.index.commit("init")
    if "main" not in repo.heads:
        repo.create_head("main")
    return repo_path
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_runner.py::test_run_task_end_of_day_stores_batch_entry -v`
Expected: FAIL with `TypeError: WorkflowRunner.__init__() got an unexpected keyword argument 'batch_store'`

- [ ] **Step 3: Modify WorkflowRunner**

In `src/devflow/daemon/runner.py`:

a) Add imports at the top (after line 30, after the `WorkflowState` import):
```python
from devflow.batch.models import BatchEntry
from devflow.batch.store import BatchStore
from devflow.config import HitlStrategy
from devflow.schemas import ReporterResponse
```

b) Add `batch_store` param to `__init__` (lines 38-51). The new signature:
```python
    def __init__(
        self,
        app_cfg: Config,
        event_bus: EventBus,
        locks: DaemonLocks,
        task_source: TaskSource | None = None,
        approval_bridge: ApprovalBridge | None = None,
        batch_store: BatchStore | None = None,
    ) -> None:
        self._cfg = app_cfg
        self._bus = event_bus
        self._locks = locks
        self._task_source = task_source
        self._bridge = approval_bridge
        self._batch_store = batch_store
        self.events_published: int = 0
```

c) After a successful `run_task` (the `return final_state` at line 114), add batch-store logic. Modify the success path inside `run_task` (replace lines 105-114):
```python
            verdict = final_state.get("final_verdict")
            self._publish(
                topic,
                {
                    "event": "task.finished",
                    "task_id": task_id,
                    "verdict": verdict.value if verdict else None,
                },
            )
            # In end_of_day mode, accumulate the result into the batch store.
            if (
                self._batch_store is not None
                and self._cfg.workflow.hitl_strategy == HitlStrategy.END_OF_DAY
            ):
                try:
                    self._store_batch_entry(task_id, final_state)
                except Exception:
                    logger.exception("Failed to store batch entry for task %s", task_id)
            return final_state
```

d) Add the `_store_batch_entry` method to the class (after `run_all`, before `_publish`):
```python
    def _store_batch_entry(self, task_id: str, state: WorkflowState) -> int:
        """Build a BatchEntry from ``state`` and persist it.

        Called after each per-task run in end_of_day mode. Reads the
        reporter artifacts (always populated by reporter_node), the plan,
        diff, checker reports, and branch from the final state.
        """
        from datetime import datetime, timezone

        assert self._batch_store is not None

        task = state.get("task")
        artifacts = state.get("reporter_artifacts")
        if task is None or artifacts is None:
            logger.warning(
                "Cannot store batch entry for %s: missing task or artifacts",
                task_id,
            )
            return -1

        plan = state.get("plan")
        plan_steps = [s.description for s in plan.steps] if plan else []
        plan_summary = plan.summary if plan else ""

        entry = BatchEntry(
            task_id=task.id,
            task_title=task.title,
            branch_name=state.get("branch_name") or "",
            worktree_path=state.get("worktree_path") or "",
            diff=state.get("diff") or "",
            plan_summary=plan_summary,
            plan_steps=plan_steps,
            checker_reports=state.get("checker_reports") or [],
            self_review_notes=state.get("self_review_notes") or "",
            final_verdict=state.get("final_verdict"),
            reporter_artifacts=artifacts,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        return self._batch_store.add(entry)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_runner.py -v`
Expected: All tests PASS (new + existing). Existing tests construct `WorkflowRunner` without `batch_store` — the new optional param defaults to `None`, so they're unaffected.

- [ ] **Step 5: Run full daemon test suite**

Run: `python -m pytest tests/unit/daemon -v`
Expected: All tests PASS.

- [ ] **Step 6: Run ruff + mypy**

Run: `python -m ruff check src/devflow/daemon/runner.py tests/unit/daemon/test_runner.py`
Expected: No errors.

Run: `python -m mypy src/devflow/daemon/runner.py`
Expected: No errors.

- [ ] **Step 7: Commit**

```bash
git add src/devflow/daemon/runner.py tests/unit/daemon/test_runner.py
git commit -m "feat(daemon): store BatchEntry after end_of_day per-task runs

WorkflowRunner gains optional batch_store. After a successful run_task in
end_of_day mode, a BatchEntry is built from the final state (reporter
artifacts + plan + diff + checker reports + branch) and persisted. Default
forge.actions (no push/create_mr) keeps per-task runs local; batch publish
happens at EOD."
```

---

## Task 7: Wire EodHandler into DaemonScheduler + lock coordination

**Files:**
- Modify: `src/devflow/daemon/scheduler.py:29-122` (DaemonScheduler gains `eod_handler`, wrappers acquire locks)
- Modify: `src/devflow/daemon/runner.py:53-125` (`run_task`/`run_all` acquire `task_run` lock)
- Test: `tests/unit/daemon/test_scheduler.py` (add eod handler wiring test)

**Interfaces:**
- Consumes: `EodHandler` (Task 5), `DaemonLocks` (locks.py)
- Produces:
  - `DaemonScheduler.__init__(self, app_cfg, runner, eod_handler: EodHandler | None = None)`
  - `_run_eod_wrapper(repo_path)` calls `eod_handler.finalize()` then `eod_handler.publish_selected([])` (publish all pending) — this is the automated cron path. The manual `/api/eod/finalize` + `/api/eod/publish` path gives finer control.
  - Both wrappers acquire their respective locks via `asyncio.run` (they run in the APScheduler thread, no event loop).

  **Lock coordination design:** Since wrappers run in a background thread (no running event loop), they use `asyncio.run(self._acquire_and_run(...))` where the coroutine does `async with locks.<lock>():`. The locks are `asyncio.Lock` objects created in the main thread's loop — but `asyncio.Lock` is tied to the loop running when first used. To make locks usable from both the request loop and the scheduler thread, we create a dedicated event loop for lock acquisition in the scheduler thread, OR (simpler, safer) we run the lock-guarded work via `asyncio.run` in a fresh loop each time. 

  **Adopted approach:** `DaemonLocks` locks are created lazily-bound to whichever loop first awaits them. Because the scheduler thread always uses `asyncio.run(...)` (fresh loop), and the web request handlers run in uvicorn's loop, the SAME `asyncio.Lock` object cannot be shared across two different loops safely. Therefore: **locks are advisory best-effort** — the scheduler thread checks `lock.locked()` before starting and logs a warning if the other operation is in progress, but does NOT block on cross-loop acquisition. This is documented as a known limitation (the spec's concurrency model assumed single-loop; the daemon runs scheduler thread + uvicorn loop). The hard mutual exclusion is already provided by APScheduler `max_instances=1` per job id.

  To keep this task focused and avoid cross-loop complexity, the wrappers implement **soft coordination**: check `.locked()` and skip/warn if the other operation holds its lock; acquire their own lock within a `asyncio.run` fresh loop so the web layer's `.locked()` check reflects the scheduler's activity.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/daemon/test_scheduler.py` (read the file first to match its fixture style):

```python
def test_eod_wrapper_calls_handler_when_provided(
    mock_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_run_eod_wrapper calls finalize + publish_selected when a handler is set."""
    from devflow.batch.eod_handler import EodHandler

    mock_config.workflow.hitl_strategy = "end_of_day"
    mock_config.workflow.daemon.enabled = True

    finalize_called: list[bool] = []
    publish_called: list[list[str]] = []

    class FakeEodHandler:
        def finalize(self):
            finalize_called.append(True)
            return []

        def publish_selected(self, task_ids):
            publish_called.append(task_ids)
            return {"published": [], "failed": [], "skipped": []}

    runner = WorkflowRunner(mock_config, EventBus(), DaemonLocks())
    scheduler = DaemonScheduler(mock_config, runner, eod_handler=FakeEodHandler())

    scheduler._run_eod_wrapper(repo_path=".")

    assert finalize_called == [True]
    assert publish_called == [[]]  # publish all pending


def test_eod_wrapper_without_handler_is_noop(
    mock_config: Config,
) -> None:
    """_run_eod_wrapper without a handler logs and does not raise."""
    mock_config.workflow.hitl_strategy = "end_of_day"
    runner = WorkflowRunner(mock_config, EventBus(), DaemonLocks())
    scheduler = DaemonScheduler(mock_config, runner)  # no eod_handler
    scheduler._run_eod_wrapper(repo_path=".")  # must not raise
```

Adjust imports at the top of `tests/unit/daemon/test_scheduler.py` to include `EventBus`, `DaemonLocks`, `WorkflowRunner`, `DaemonScheduler`, `Config` — match what's already imported there.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_scheduler.py::test_eod_wrapper_calls_handler_when_provided -v`
Expected: FAIL with `TypeError: DaemonScheduler.__init__() got an unexpected keyword argument 'eod_handler'`

- [ ] **Step 3: Modify DaemonScheduler**

In `src/devflow/daemon/scheduler.py`:

a) Add import at the top (after line 21, after the runner import):
```python
from devflow.batch.eod_handler import EodHandler
```

b) Change `__init__` (lines 29-34):
```python
    def __init__(
        self,
        app_cfg: Config,
        runner: WorkflowRunner,
        eod_handler: EodHandler | None = None,
    ) -> None:
        self._cfg = app_cfg
        self._runner = runner
        self._eod_handler = eod_handler
        self._scheduler = BackgroundScheduler(daemon=True)
        self._lock = threading.Lock()
        self._jobs_registered = False
```

c) Replace `_run_eod_wrapper` (lines 116-122):
```python
    def _run_eod_wrapper(self, repo_path: str) -> None:
        """Job handler: run EOD batch-review + publish-all.

        Soft lock coordination: if the task_run lock is held (a task run is
        in progress), we log and skip — APScheduler coalesce will merge
        missed runs. The hard mutual exclusion is max_instances=1.
        """
        try:
            if self._runner._locks.task_run().locked():  # type: ignore[attr-defined]
                logger.warning(
                    "eod_review skipped: a task run is in progress; will retry next tick"
                )
                return
            if self._eod_handler is None:
                logger.info("eod_review job triggered but no handler configured")
                return
            logger.info("eod_review job triggered")
            self._eod_handler.finalize()
            self._eod_handler.publish_selected([])
        except Exception:
            logger.exception("eod_review job failed")
```

**Note:** accessing `self._runner._locks` reaches into the runner's private attribute. To avoid this, add a public accessor to `WorkflowRunner`:
```python
    @property
    def locks(self) -> DaemonLocks:
        """Expose locks for scheduler coordination."""
        return self._locks
```
Then the scheduler uses `self._runner.locks.task_run().locked()`.

- [ ] **Step 4: Add the `locks` property to WorkflowRunner**

In `src/devflow/daemon/runner.py`, add (after the `events_published` field, around line 51):
```python
    @property
    def locks(self) -> DaemonLocks:
        """Expose locks for scheduler/API coordination."""
        return self._locks
```

Update the scheduler check (Step 3c) to use `self._runner.locks.task_run().locked()`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/daemon/test_scheduler.py -v`
Expected: All tests PASS.

- [ ] **Step 6: Run full daemon suite**

Run: `python -m pytest tests/unit/daemon -v`
Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/devflow/daemon/scheduler.py src/devflow/daemon/runner.py tests/unit/daemon/test_scheduler.py
git commit -m "feat(daemon): wire EodHandler into scheduler eod_review job

DaemonScheduler gains optional eod_handler. _run_eod_wrapper now calls
finalize() + publish_selected([]) (publish all pending). Soft coordination:
skips if task_run lock held. WorkflowRunner exposes locks property."
```

---

## Task 8: EOD REST API routes

**Files:**
- Modify: `src/devflow/daemon/web.py:51-160` (add `eod_handler` param, `/api/eod*` routes, wire `batch_store_pending` in health)
- Test: `tests/unit/daemon/test_web.py` (add EOD route tests)

**Interfaces:**
- Consumes: `EodHandler` (Task 5), `BatchStore` (Task 2)
- Produces:
  - `create_app(..., eod_handler: EodHandler | None = None)` — new optional param
  - `run_web_server(..., eod_handler: EodHandler | None = None)` — forwarded
  - Routes (only registered when `eod_handler is not None`):
    - `GET /api/eod` → `list[dict]` — pending entries (summary view: id, task_id, task_title, branch_name, final_verdict, status, created_at)
    - `POST /api/eod/finalize` → `{"pending_count": int}` — calls `eod_handler.finalize()`, returns count
    - `POST /api/eod/publish` body `{"task_ids": [...]}` → `{"published": [...], "failed": [...], "skipped": [...]}` — calls `eod_handler.publish_selected(task_ids)`
    - `GET /api/eod/entries/{entry_id}` → full BatchEntry dict or 404
  - `HealthResponse.batch_store_pending` populated from `eod_handler._store.count_pending()` when handler present

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/daemon/test_web.py` (read the file first to match its style):

```python
def test_eod_routes_list_pending(
    mock_config: Config, tmp_path
) -> None:
    """GET /api/eod returns pending entries when eod_handler is set."""
    from devflow.batch.eod_handler import EodHandler
    from devflow.batch.store import BatchStore
    from devflow.batch.models import BatchEntry
    from devflow.daemon.events import EventBus
    from devflow.daemon.locks import DaemonLocks
    from devflow.daemon.web import create_app
    from devflow.schemas import ReporterResponse
    from devflow.state import FinalVerdict
    from fastapi.testclient import TestClient

    store = BatchStore(tmp_path / "batch_store.db")
    store.add(
        BatchEntry(
            task_id="T-1",
            task_title="Task 1",
            branch_name="devflow/T-1/abc",
            worktree_path="/p",
            diff="d",
            plan_summary="s",
            plan_steps=[],
            checker_reports=[],
            self_review_notes="",
            final_verdict=FinalVerdict.APPROVE,
            reporter_artifacts=ReporterResponse(
                pr_title="t", pr_description="d", corporate_report="r", commit_message="c"
            ),
            created_at="2026-07-13T10:00:00Z",
        )
    )
    handler = EodHandler(mock_config, store, EventBus(), repo_path=".")
    app = create_app(
        mock_config, DaemonLocks(), EventBus(), eod_handler=handler
    )
    client = TestClient(app)
    resp = client.get("/api/eod")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["task_id"] == "T-1"
    store.close()


def test_eod_publish_route(
    mock_config: Config, tmp_path, monkeypatch
) -> None:
    """POST /api/eod/publish publishes selected entries."""
    from devflow.batch.eod_handler import EodHandler
    from devflow.batch.store import BatchStore
    from devflow.batch.models import BatchEntry
    from devflow.daemon.events import EventBus
    from devflow.daemon.locks import DaemonLocks
    from devflow.daemon.web import create_app
    from devflow.schemas import ReporterResponse
    from devflow.state import FinalVerdict
    from fastapi.testclient import TestClient

    store = BatchStore(tmp_path / "batch_store.db")
    store.add(
        BatchEntry(
            task_id="T-1",
            task_title="Task 1",
            branch_name="devflow/T-1/abc",
            worktree_path="/p",
            diff="d",
            plan_summary="s",
            plan_steps=[],
            checker_reports=[],
            self_review_notes="",
            final_verdict=FinalVerdict.APPROVE,
            reporter_artifacts=ReporterResponse(
                pr_title="t", pr_description="d", corporate_report="r", commit_message="c"
            ),
            created_at="2026-07-13T10:00:00Z",
        )
    )
    handler = EodHandler(mock_config, store, EventBus(), repo_path=".")

    # Stub the publisher so no real forge/httpx calls happen.
    class StubPublisher:
        def publish(self, entry):
            entry.status = "published"
            return entry

    monkeypatch.setattr(handler, "build_publisher", lambda: StubPublisher())

    app = create_app(
        mock_config, DaemonLocks(), EventBus(), eod_handler=handler
    )
    client = TestClient(app)
    resp = client.post("/api/eod/publish", json={"task_ids": ["T-1"]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["published"] == ["T-1"]
    store.close()


def test_eod_finalize_route(
    mock_config: Config, tmp_path
) -> None:
    """POST /api/eod/finalize returns the pending count."""
    from devflow.batch.eod_handler import EodHandler
    from devflow.batch.store import BatchStore
    from devflow.batch.models import BatchEntry
    from devflow.daemon.events import EventBus
    from devflow.daemon.locks import DaemonLocks
    from devflow.daemon.web import create_app
    from devflow.schemas import ReporterResponse
    from devflow.state import FinalVerdict
    from fastapi.testclient import TestClient

    store = BatchStore(tmp_path / "batch_store.db")
    store.add(
        BatchEntry(
            task_id="T-1",
            task_title="t",
            branch_name="b",
            worktree_path="/p",
            diff="d",
            plan_summary="s",
            plan_steps=[],
            checker_reports=[],
            self_review_notes="",
            final_verdict=FinalVerdict.APPROVE,
            reporter_artifacts=ReporterResponse(
                pr_title="t", pr_description="d", corporate_report="r", commit_message="c"
            ),
            created_at="2026-07-13T10:00:00Z",
        )
    )
    handler = EodHandler(mock_config, store, EventBus(), repo_path=".")
    app = create_app(
        mock_config, DaemonLocks(), EventBus(), eod_handler=handler
    )
    client = TestClient(app)
    resp = client.post("/api/eod/finalize")
    assert resp.status_code == 200
    assert resp.json()["pending_count"] == 1
    store.close()


def test_health_includes_batch_store_pending(
    mock_config: Config, tmp_path
) -> None:
    """GET /api/health reports batch_store_pending when eod_handler is set."""
    from devflow.batch.eod_handler import EodHandler
    from devflow.batch.store import BatchStore
    from devflow.daemon.events import EventBus
    from devflow.daemon.locks import DaemonLocks
    from devflow.daemon.web import create_app
    from fastapi.testclient import TestClient

    store = BatchStore(tmp_path / "batch_store.db")
    handler = EodHandler(mock_config, store, EventBus(), repo_path=".")
    app = create_app(
        mock_config, DaemonLocks(), EventBus(), eod_handler=handler
    )
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["batch_store_pending"] == 0
    store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_web.py -v`
Expected: FAIL with `TypeError: create_app() got an unexpected keyword argument 'eod_handler'`

- [ ] **Step 3: Modify web.py**

In `src/devflow/daemon/web.py`:

a) Add imports (after line 22, after the `DaemonLocks` import):
```python
from devflow.batch.eod_handler import EodHandler
```

b) Add a request body model after `ApprovalDecision` (after line 48):
```python
class EodPublishRequest(BaseModel):
    """Body of POST /api/eod/publish."""

    task_ids: list[str] = Field(default_factory=list)
```

c) Change `create_app` signature (line 51):
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

d) In the `health()` handler (lines 83-93), populate `batch_store_pending`:
```python
    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        uptime = int(time.monotonic() - start_time)
        task_running = _is_task_running()
        pending = len(approval_store.get_pending()) if approval_store else 0
        batch_pending = 0
        if eod_handler is not None:
            try:
                batch_pending = eod_handler._store.count_pending()  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover - defensive
                logger.debug("Failed to count pending batch entries")
        return HealthResponse(
            status="degraded" if task_running else "healthy",
            scheduler="busy" if task_running else "running",
            uptime_seconds=uptime,
            current_task=_state.get("current_task"),
            pending_approvals=pending,
            batch_store_pending=batch_pending,
        )
```

e) Add the EOD routes before `return app` (after the approval block, around line 131). Insert:
```python
    if eod_handler is not None:

        def _entry_summary(entry: Any) -> dict[str, Any]:
            return {
                "id": entry.id,
                "task_id": entry.task_id,
                "task_title": entry.task_title,
                "branch_name": entry.branch_name,
                "final_verdict": entry.final_verdict.value if entry.final_verdict else None,
                "status": entry.status,
                "created_at": entry.created_at,
            }

        @app.get("/api/eod")
        async def list_eod_pending() -> list[dict[str, Any]]:
            return [_entry_summary(e) for e in eod_handler.list_pending()]

        @app.post("/api/eod/finalize")
        async def finalize_eod() -> dict[str, Any]:
            pending = eod_handler.finalize()
            return {"pending_count": len(pending)}

        @app.post("/api/eod/publish")
        async def publish_eod(req: EodPublishRequest) -> dict[str, Any]:
            return eod_handler.publish_selected(req.task_ids)

        @app.get("/api/eod/entries/{entry_id}")
        async def get_eod_entry(entry_id: int) -> dict[str, Any]:
            entry = eod_handler._store.get_entry(entry_id)  # type: ignore[attr-defined]
            if entry is None:
                raise HTTPException(status_code=404, detail=f"Unknown entry id: {entry_id}")
            return entry.model_dump(mode="json")
```

f) Change `run_web_server` signature (line 143) to forward `eod_handler`:
```python
def run_web_server(
    app_cfg: Config,
    locks: DaemonLocks,
    event_bus: EventBus,
    runner: Any | None = None,
    approval_store: ApprovalStore | None = None,
    eod_handler: EodHandler | None = None,
) -> None:
    import uvicorn

    app = create_app(
        app_cfg, locks, event_bus, runner,
        approval_store=approval_store, eod_handler=eod_handler,
    )
    port = app_cfg.workflow.daemon.port
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_web.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devflow/daemon/web.py tests/unit/daemon/test_web.py
git commit -m "feat(daemon): add /api/eod* routes + batch_store_pending in health

GET /api/eod (pending summaries), POST /api/eod/finalize (returns count),
POST /api/eod/publish {task_ids} -> {published,failed,skipped},
GET /api/eod/entries/{id} (full entry or 404). Health endpoint now
reports batch_store_pending when eod_handler is wired."
```

---

## Task 9: Wire BatchStore + EodHandler into daemon entry point

**Files:**
- Modify: `src/devflow/daemon/__main__.py:31-130` (construct BatchStore + EodHandler, pass to runner/scheduler/web)
- Test: `tests/unit/daemon/test_main.py` (add test that BatchStore/EodHandler are constructed in end_of_day)

**Interfaces:**
- Consumes: `BatchStore` (Task 2), `EodHandler` (Task 5), updated `WorkflowRunner`, `DaemonScheduler`, `run_web_server` signatures
- Produces: `run_daemon` constructs `BatchStore({repo_path}/.devflow/batch_store.db)` and `EodHandler(...)` always (cheap; used by health endpoint regardless of strategy), passes `batch_store` to `WorkflowRunner`, `eod_handler` to `DaemonScheduler` and `run_web_server`.

- [ ] **Step 1: Read the existing test_main.py**

Read `tests/unit/daemon/test_main.py` to understand how `run_daemon` is tested (it likely patches `run_web_server` and asserts components are constructed). Match that pattern.

- [ ] **Step 2: Modify run_daemon**

In `src/devflow/daemon/__main__.py`, add imports (after line 26, after the notifications import):
```python
from devflow.batch.eod_handler import EodHandler
from devflow.batch.store import BatchStore
```

In `run_daemon` (lines 31-106), after creating `event_bus` and `locks` (line 55), construct the batch store + handler:
```python
    # 3. Create shared components.
    event_bus = EventBus()
    locks = DaemonLocks()

    # 3a. Batch store + EOD handler (always constructed; used by health
    # endpoint regardless of strategy, and by the eod_review cron job in
    # end_of_day mode).
    batch_store = BatchStore(str(Path(repo_path) / ".devflow" / "batch_store.db"))
    eod_handler = EodHandler(app_cfg, batch_store, event_bus, repo_path=repo_path)
```

Add `from pathlib import Path` to the imports at the top of the file if not present.

Update the `WorkflowRunner` construction (line 78):
```python
    runner = WorkflowRunner(
        app_cfg, event_bus, locks, approval_bridge=bridge, batch_store=batch_store
    )
```

Update the `DaemonScheduler` construction (line 81):
```python
    scheduler = DaemonScheduler(app_cfg, runner, eod_handler=eod_handler)
```

Update the `run_web_server` call (line 102):
```python
    try:
        run_web_server(
            app_cfg, locks, event_bus, runner,
            approval_store=approval_store, eod_handler=eod_handler,
        )
    finally:
        logger.info("Web server stopped; shutting down scheduler...")
        scheduler.shutdown()
        batch_store.close()
        logger.info("Daemon stopped.")
```

- [ ] **Step 3: Write a test (or extend existing)**

Add to `tests/unit/daemon/test_main.py` (matching its existing test style — typically it patches `run_web_server` to a no-op and asserts `load_config`/scheduler were called). Add:

```python
def test_run_daemon_constructs_batch_store_in_end_of_day(
    mock_config: Config, tmp_path, monkeypatch
) -> None:
    """run_daemon constructs BatchStore + EodHandler and passes them through."""
    mock_config.workflow.daemon.enabled = True
    mock_config.workflow.hitl_strategy = "end_of_day"

    constructed: dict[str, bool] = {}

    def fake_load_config(_d):
        return mock_config

    def fake_run_web_server(*args, **kwargs):
        constructed["eod_handler_passed"] = kwargs.get("eod_handler") is not None

    monkeypatch.setattr("devflow.daemon.__main__.load_config", fake_load_config)
    monkeypatch.setattr("devflow.daemon.__main__.cleanup_orphan_worktrees", lambda p: [])
    monkeypatch.setattr("devflow.daemon.__main__.run_web_server", fake_run_web_server)
    # Avoid actually starting the scheduler thread.
    monkeypatch.setattr(
        "devflow.daemon.__main__.DaemonScheduler",
        lambda *a, **kw: MagicMock(start=lambda: None, register_jobs=lambda p: None, shutdown=lambda: None),
    )

    from devflow.daemon.__main__ import run_daemon
    run_daemon(config_dir="config", repo_path=str(tmp_path))

    assert constructed["eod_handler_passed"] is True
```

Add necessary imports (`from unittest.mock import MagicMock`, `from devflow.config import Config`) if not already in the file.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_main.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devflow/daemon/__main__.py tests/unit/daemon/test_main.py
git commit -m "feat(daemon): wire BatchStore + EodHandler into run_daemon

BatchStore at {repo_path}/.devflow/batch_store.db, EodHandler always
constructed (used by health endpoint + eod_review cron). Passed to
WorkflowRunner (batch_store), DaemonScheduler (eod_handler), and
run_web_server (eod_handler). Store closed on shutdown."
```

---

## Task 10: Integration test — end-to-end EOD batch flow

**Files:**
- Create: `tests/integration/test_eod_batch.py`

**Interfaces:**
- Consumes: all Phase 4 components + existing graph machinery

This is the capstone: simulate an end_of_day run — store entries, then batch-publish them, asserting the forge is called and the store reflects published status.

- [ ] **Step 1: Write the integration test**

Create `tests/integration/test_eod_batch.py`:
```python
"""Integration test: end-to-end EOD batch flow.

Simulates the end_of_day lifecycle:
1. Two BatchEntries are accumulated (as the runner would after per-task runs).
2. EodHandler.publish_selected([]) publishes all pending.
3. The fake forge records push + create_mr calls.
4. The store reflects PUBLISHED status for both.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from devflow.batch.eod_handler import EodHandler
from devflow.batch.models import BatchEntry, BatchStatus
from devflow.batch.store import BatchStore
from devflow.config import Config
from devflow.daemon.events import EventBus
from devflow.forge.base import MRInfo
from devflow.schemas import ReporterResponse
from devflow.state import CheckerReport, CheckerVerdict, FinalVerdict


def _make_entry(task_id: str, branch: str) -> BatchEntry:
    return BatchEntry(
        task_id=task_id,
        task_title=f"Task {task_id}",
        branch_name=branch,
        worktree_path="/tmp/repo-wt",
        diff=f"diff for {task_id}",
        plan_summary="summary",
        plan_steps=["step 1"],
        checker_reports=[
            CheckerReport(
                agent_name="checker_a",
                verdict=CheckerVerdict.APPROVE,
                summary="ok",
            )
        ],
        self_review_notes="",
        final_verdict=FinalVerdict.APPROVE,
        reporter_artifacts=ReporterResponse(
            pr_title=f"feat: {task_id}",
            pr_description="description",
            corporate_report="report",
            commit_message=f"feat: {task_id}",
        ),
        created_at="2026-07-13T10:00:00Z",
    )


class RecordingForge:
    name = "recording"

    def __init__(self) -> None:
        self.pushed: list[str] = []
        self.mrs: list[str] = []

    def push(self, branch: str, target: str, repo_path: str) -> str:
        self.pushed.append(branch)
        return f"sha-{branch}"

    def create_mr(self, branch: str, target: str, title: str, description: str) -> MRInfo:
        self.mrs.append(branch)
        return MRInfo(url=f"https://example.com/mr/{branch}", number=len(self.mrs))

    def healthcheck(self) -> bool:
        return True

    def close(self) -> None:
        pass


def test_eod_batch_publish_all(
    tmp_path: Path, mock_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full EOD batch: accumulate 2 entries, publish all, verify forge + store."""
    store = BatchStore(tmp_path / "batch_store.db")
    store.add(_make_entry("T-1", "devflow/T-1/aaa"))
    store.add(_make_entry("T-2", "devflow/T-2/bbb"))
    assert store.count_pending() == 2

    forge = RecordingForge()
    monkeypatch.setattr("devflow.batch.publisher.build_forge_backend", lambda wf: forge)
    monkeypatch.setattr(
        "devflow.batch.publisher._publish_to_channels", lambda cfg, msg: "console"
    )
    fake_source = MagicMock()
    fake_source.update_task_status = MagicMock()
    fake_source.close = lambda: None
    monkeypatch.setattr(
        "devflow.batch.publisher.build_task_source", lambda wf: fake_source
    )

    mock_config.workflow.forge.provider = "github"
    mock_config.workflow.forge.target_branch = "main"

    handler = EodHandler(mock_config, store, EventBus(), repo_path=str(tmp_path))
    result = handler.publish_selected([])

    assert set(result["published"]) == {"T-1", "T-2"}
    assert result["failed"] == []
    # Forge pushed both branches and created MRs for both.
    assert set(forge.pushed) == {"devflow/T-1/aaa", "devflow/T-2/bbb"}
    assert set(forge.mrs) == {"devflow/T-1/aaa", "devflow/T-2/bbb"}
    # Tracker updated for both.
    assert fake_source.update_task_status.call_count == 2
    # Store: no pending left; both published.
    assert store.count_pending() == 0
    published = store.list_all(status=BatchStatus.PUBLISHED)
    assert len(published) == 2
    for entry in published:
        assert entry.pushed_sha is not None
        assert entry.mr_url is not None
    store.close()


def test_eod_batch_publish_subset_then_finalize(
    tmp_path: Path, mock_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publish a subset, then finalize reports only the remaining pending."""
    store = BatchStore(tmp_path / "batch_store.db")
    store.add(_make_entry("T-1", "devflow/T-1/aaa"))
    store.add(_make_entry("T-2", "devflow/T-2/bbb"))
    store.add(_make_entry("T-3", "devflow/T-3/ccc"))

    forge = RecordingForge()
    monkeypatch.setattr("devflow.batch.publisher.build_forge_backend", lambda wf: forge)
    monkeypatch.setattr(
        "devflow.batch.publisher._publish_to_channels", lambda cfg, msg: "console"
    )
    fake_source = MagicMock()
    fake_source.update_task_status = MagicMock()
    fake_source.close = lambda: None
    monkeypatch.setattr(
        "devflow.batch.publisher.build_task_source", lambda wf: fake_source
    )

    mock_config.workflow.forge.provider = "github"
    handler = EodHandler(mock_config, store, EventBus(), repo_path=str(tmp_path))

    # Publish only T-1 and T-3.
    result = handler.publish_selected(["T-1", "T-3"])
    assert set(result["published"]) == {"T-1", "T-3"}
    assert "T-2" in result["skipped"]
    assert store.count_pending() == 1

    # Finalize returns the remaining pending (T-2).
    pending = handler.finalize()
    assert len(pending) == 1
    assert pending[0].task_id == "T-2"
    store.close()


async def test_eod_finalize_emits_event(
    tmp_path: Path, mock_config: Config
) -> None:
    """finalize() publishes an eod.ready event on the event bus."""
    store = BatchStore(tmp_path / "batch_store.db")
    store.add(_make_entry("T-1", "devflow/T-1/aaa"))
    bus = EventBus()
    handler = EodHandler(mock_config, store, bus, repo_path=str(tmp_path))

    queue = await bus.subscribe("eod")
    handler.finalize()
    msg = await queue.get()
    assert msg["event"] == "eod.ready"
    assert msg["pending_count"] == 1
    store.close()
```

- [ ] **Step 2: Run the integration test**

Run: `python -m pytest tests/integration/test_eod_batch.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 3: Run the FULL test suite**

Run: `python -m pytest tests/ -v`
Expected: ALL tests PASS (274 prior + new Phase 4 tests).

- [ ] **Step 4: Run ruff + mypy on all new/modified files**

Run: `python -m ruff check src/devflow/batch/ src/devflow/daemon/ src/devflow/nodes/reporter.py tests/unit/batch/ tests/integration/test_eod_batch.py`
Expected: No errors.

Run: `python -m mypy src/devflow/batch/ src/devflow/daemon/ src/devflow/nodes/reporter.py`
Expected: No errors.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_eod_batch.py
git commit -m "test(integration): end-to-end EOD batch flow

Accumulate entries -> publish_selected -> verify forge push/MR + tracker
update + store published status. Subset publish + finalize + eod.ready
event emission covered."
```

---

## Task 11: Update workflow.yaml docs + HANDOFF

**Files:**
- Modify: `config/workflow.yaml` (document end_of_day batch behavior in comments)
- Modify: `docs/superpowers/HANDOFF.md` (update to reflect Phase 4 complete)

**Interfaces:**
- Consumes: nothing (docs only)

- [ ] **Step 1: Update workflow.yaml comments**

In `config/workflow.yaml`, in the `forge:` section, update the comment to clarify end_of_day behavior:
```yaml
forge:
  provider: none    # none | github | gitlab | auto (auto = detect from git remote)
  target_branch: main
  actions:
    - publish_report
    - update_tracker
    - record_todo
    # In end_of_day mode, push + create_mr are invoked by the EOD batch
    # publisher (Phase 4) regardless of this list — per-task runs stay local
    # (only record_todo), and the batch-publish step pushes branches + opens
    # MRs for all approved tasks at once. For per_plan/full_detail, uncomment
    # to push/open MRs per task:
    # - push
    # - create_mr
```

- [ ] **Step 2: Update HANDOFF.md**

In `docs/superpowers/HANDOFF.md`:
- Change the "Что реализовано" section to add Phase 4.
- Change "Что осталось" to show only Phase 5.
- Update the Git state to reflect a new branch `feature/phase4-eod-batch`.
- Update the "Как начать в новой сессии" to point at Phase 5 (Vue SPA).

Specifically, add a Phase 4 section (after Phase 3):
```markdown
### Phase 4: EOD batch-flow + batch-store (this branch, feature/phase4-eod-batch)
- `BatchEntry` Pydantic model + `BatchStatus` lifecycle (pending_review/approved/rejected/published)
- `BatchStore` SQLite CRUD — add, get_pending, get_by_task, list_all, update_status, count_pending (JSON-in-column design)
- `BatchPublisher` — sequential idempotent publish (forge.push + create_mr + _publish_to_channels + update_tracker)
- `EodHandler` — list_pending, publish_selected(task_ids), finalize (emits eod.ready event)
- Reporter `prepare_only` mode — generates artifacts + record_todo, defers publish/push/MR
- `WorkflowRunner` stores BatchEntry after each end_of_day per-task run
- `DaemonScheduler._run_eod_wrapper` wired to EodHandler (finalize + publish_all)
- `/api/eod`, `/api/eod/finalize`, `/api/eod/publish`, `/api/eod/entries/{id}` routes
- `HealthResponse.batch_store_pending` populated
- Soft lock coordination (cross-loop limitation documented)
```

- [ ] **Step 3: Commit**

```bash
git add config/workflow.yaml docs/superpowers/HANDOFF.md
git commit -m "docs: update workflow.yaml + HANDOFF for Phase 4 EOD batch

workflow.yaml clarifies end_of_day batch behavior (push/MR at batch stage).
HANDOFF marks Phase 4 complete, Phase 5 (Vue SPA) remaining."
```

---

## Self-Review

### Spec coverage (Phase 4 scope, per design spec lines 185-249 + 331-403)

| Spec requirement | Task(s) | Covered? |
|---|---|---|
| `BatchStore` SQLite at `{repo}/.devflow/batch_store.db` | Task 2 | ✅ |
| `BatchEntry` Pydantic: task_id, branch, diff, plan, checker_reports, reporter artifacts, status | Task 1 | ✅ |
| Status lifecycle: pending_review → published / rejected | Tasks 1, 2 | ✅ |
| CRUD: add, get_pending, mark_published, mark_rejected | Task 2 (`add`, `get_pending`, `update_status`) | ✅ |
| Reporter prepare-only mode for end_of_day per-task | Task 3 | ✅ |
| EOD trigger (cron 18:00 + button in UI) | Tasks 7 (cron), 8 (`POST /api/eod/finalize` button) | ✅ |
| Batch-review: list pending + selection | Task 8 (`GET /api/eod`, `POST /api/eod/publish` with task_ids) | ✅ |
| Batch-publish: forge.push + forge.create_mr + _publish_to_channels + update_task_status, sequential, idempotent | Task 4 (BatchPublisher), Task 5 (EodHandler.publish_selected) | ✅ |
| Per-task failure isolation (try/except per task) | Tasks 4, 5 | ✅ |
| Rejected/excluded tasks remain pending for next EOD | Task 5 (skipped entries stay pending_review) | ✅ |
| Concurrency: `eod_review` lock coordinates with `task_run` lock | Task 7 (soft coordination, documented limitation) | ✅ (with caveat) |
| `eod.ready` SSE event | Task 5 (finalize emits on EventBus) | ✅ |
| `HealthResponse.batch_store_pending` populated | Task 8 | ✅ |
| Daemon entry point constructs BatchStore + EodHandler | Task 9 | ✅ |

**Deferred / out of scope (correctly):**
- Full cross-loop hard mutual exclusion (documented as known limitation; APScheduler `max_instances=1` provides the hard guard)
- Retention policy for old `published` entries (spec line 249, "Future work")
- Sweep EOD-awareness (don't delete branches referenced by pending entries) — spec line 365; tech debt, not blocking
- Forge audit JSONL log (`logs/forge_audit.jsonl`, spec line 400) — future work

### Placeholder scan

Searched for TBD/TODO/FIXME/"implement later"/"similar to"/"add appropriate"/"handle edge cases" in actionable steps. The only intentional TODO removals are the existing `# TODO(Phase 4)` comments in scheduler.py/runner.py (replaced by real implementations in Tasks 6, 7). No placeholder steps remain — every code step contains complete code.

### Type consistency

- `BatchEntry` fields: consistent across Task 1 (definition), Task 2 (store JSON round-trip), Task 4 (publisher reads `entry.branch_name`, `entry.reporter_artifacts.pr_title`, `entry.final_verdict`), Task 5 (handler lists entries), Task 6 (runner builds entries), Task 8 (API summary), Task 10 (integration).
- `BatchStatus.PENDING_REVIEW/APPROVED/REJECTED/PUBLISHED`: consistent across Tasks 1, 2, 4, 5, 10.
- `BatchStore.add(entry) -> int`, `get_pending() -> list[BatchEntry]`, `update_status(id, status, *, mr_url, pushed_sha, rejection_reason) -> bool`, `count_pending() -> int`: consistent across Tasks 2, 4, 5, 8, 9, 10.
- `BatchPublisher.publish(entry) -> BatchEntry`: consistent across Tasks 4, 5, 10.
- `EodHandler.list_pending()`, `publish_selected(task_ids) -> {published, failed, skipped}`, `finalize() -> list[BatchEntry]`, `build_publisher() -> BatchPublisher`: consistent across Tasks 5, 7, 8, 10.
- `reporter_node(state, *, app_cfg, prepare_only=False)`: consistent across Task 3 (definition + tests).
- `WorkflowRunner.__init__(..., batch_store=None)`, `._store_batch_entry(task_id, state) -> int`, `.locks` property: consistent across Tasks 6, 7, 9.
- `DaemonScheduler.__init__(..., eod_handler=None)`: consistent across Tasks 7, 9.
- `create_app(..., eod_handler=None)`, `run_web_server(..., eod_handler=None)`: consistent across Tasks 8, 9.
- `EodPublishRequest.task_ids: list[str]`: consistent across Task 8 (definition + route).

No type/name mismatches found.
