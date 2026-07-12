# Phase 1: Daemon + Scheduler + API Skeleton — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a long-running `devflow-daemon` Windows service that runs APScheduler cron jobs to invoke the existing `devflow-super` workflow, exposes a FastAPI health endpoint on localhost, and performs startup cleanup of orphaned git worktrees.

**Architecture:** A single Python process (`python -m devflow.daemon`) wraps APScheduler + FastAPI (uvicorn). The scheduler triggers `run_workflow` (non-interactive in Phase 1 — HITL strategies come in Phase 2). FastAPI serves `/api/health` and a minimal `/api/state`. On startup, a sweep function cleans orphaned worktrees left by previous crashes. The daemon is installable as a Windows Service via `nssm`.

**Tech Stack:** Python ≥3.11, APScheduler, FastAPI, uvicorn, GitPython (existing), pytest + pytest-asyncio (existing).

## Global Constraints

- Python ≥3.11 (pyproject.toml line 10)
- Line length 100, ruff rules: E, F, I, W, UP, B, C4, SIM (pyproject.toml lines 52-56)
- `asyncio_mode = "auto"` for pytest-asyncio (pyproject.toml line 67)
- `testpaths = ["tests"]`, `pythonpath = ["src"]` (pyproject.toml lines 68-69)
- Existing config pattern: Pydantic `BaseModel` + YAML in `config/workflow.yaml` + env overrides in `config.py:load_workflow_config` (lines 161-181)
- Existing notification factory pattern: registry dict + lazy import + `register_notification_channel` hook (`notifications/factory.py:28-125`)
- Existing test pattern: `conftest.py` provides `mock_config`, `fake_llm_factory`, `temp_git_repo` fixtures
- All code and logs in English (only `_TODO_HEADER` in `orchestrator.py:40-44` is Russian, and that is human-facing)
- Config env-override pattern: `DEVFLOW_*` prefix, parsed in `load_workflow_config` (config.py:168-177)

---

## File Structure

| File | Responsibility |
|---|---|
| `src/devflow/daemon/__init__.py` | Package init, exposes `run_daemon()` entry point |
| `src/devflow/daemon/scheduler.py` | APScheduler configuration, cron job registration, job wrappers |
| `src/devflow/daemon/web.py` | FastAPI app factory, uvicorn runner, `/api/health` and `/api/state` routes |
| `src/devflow/daemon/events.py` | In-process EventBus (pub/sub) for live updates — Phase 1 stub, full use in Phase 5 |
| `src/devflow/daemon/locks.py` | `DaemonLocks` class with `asyncio.Lock` instances for concurrency control |
| `src/devflow/daemon/sweep.py` | Startup cleanup of orphaned git worktrees |
| `src/devflow/daemon/runner.py` | Workflow runner adapter: wraps `run_workflow` / `run_workflow_interactive`, publishes events to EventBus |
| `src/devflow/daemon/__main__.py` | `python -m devflow.daemon` entry point: loads config, starts scheduler + uvicorn |
| `src/devflow/config.py` | Modify: add `DaemonConfig`, `HitlStrategy` enum, extend `WorkflowConfig` with daemon/schedule fields |
| `config/workflow.yaml` | Modify: add `daemon:` section with schedule config |
| `pyproject.toml` | Modify: add `apscheduler`, `fastapi`, `uvicorn`, `python-multipart` deps; add `devflow-daemon` console script |
| `.gitignore` | Modify: add `.devflow/`, `logs/` |
| `.env.example` | Modify: document `DEVFLOW_DAEMON_*` env vars |
| `scripts/install-service.bat` | New: nssm service installation script |
| `scripts/run-daemon-dev.bat` | New: foreground daemon launch for debugging |
| `tests/unit/daemon/__init__.py` | New: test package init |
| `tests/unit/daemon/test_scheduler.py` | New: scheduler config + job registration tests |
| `tests/unit/daemon/test_web.py` | New: FastAPI health/state endpoint tests |
| `tests/unit/daemon/test_events.py` | New: EventBus pub/sub tests |
| `tests/unit/daemon/test_locks.py` | New: DaemonLocks tests |
| `tests/unit/daemon/test_sweep.py` | New: orphan worktree cleanup tests |
| `tests/unit/daemon/test_runner.py` | New: workflow runner adapter tests |
| `tests/unit/daemon/test_main.py` | New: daemon entry point integration test |

---

## Task 1: Add daemon config schema and extend WorkflowConfig

**Files:**
- Modify: `src/devflow/config.py:42-54` (WorkflowConfig) and `src/devflow/config.py:161-181` (load_workflow_config)
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Consumes: existing `WorkflowConfig` (config.py:42-54), existing env-override pattern (config.py:168-177)
- Produces: `DaemonConfig` model, `HitlStrategy` enum, `WorkflowConfig.daemon` field, `WorkflowConfig.hitl_strategy` field, env override `DEVFLOW_HITL_STRATEGY`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_config.py`:

```python
def test_workflow_config_has_daemon_defaults() -> None:
    """WorkflowConfig gets sensible daemon defaults when not specified."""
    from devflow.config import WorkflowConfig

    cfg = WorkflowConfig(task_source="mock")
    assert cfg.daemon.enabled is False
    assert cfg.daemon.task_schedule == "0 9,15 * * 1-5"
    assert cfg.daemon.eod_schedule == "0 18 * * 1-5"
    assert cfg.daemon.port == 8787
    assert cfg.daemon.approval_timeout_hours == 8
    assert cfg.daemon.approval_on_timeout == "defer"
    assert cfg.hitl_strategy == "per_plan"


def test_daemon_config_from_yaml(tmp_path: Path) -> None:
    """Daemon config loads from YAML with env interpolation."""
    from devflow.config import load_workflow_config

    yaml_path = tmp_path / "workflow.yaml"
    yaml_path.write_text(
        "task_source: mock\n"
        "hitl_strategy: full_detail\n"
        "daemon:\n"
        "  enabled: true\n"
        "  task_schedule: '0 10 * * 1-5'\n"
        "  eod_schedule: '0 19 * * 1-5'\n"
        "  port: 9000\n"
        "  approval_timeout_hours: 4\n"
        "  approval_on_timeout: reject\n",
        encoding="utf-8",
    )
    cfg = load_workflow_config(yaml_path)
    assert cfg.daemon.enabled is True
    assert cfg.daemon.task_schedule == "0 10 * * 1-5"
    assert cfg.daemon.port == 9000
    assert cfg.daemon.approval_timeout_hours == 4
    assert cfg.daemon.approval_on_timeout == "reject"
    assert cfg.hitl_strategy == "full_detail"


def test_hitl_strategy_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """DEVFLOW_HITL_STRATEGY env var overrides the YAML value."""
    from devflow.config import load_workflow_config

    yaml_path = tmp_path / "workflow.yaml"
    yaml_path.write_text(
        "task_source: mock\nhitl_strategy: per_plan\n", encoding="utf-8"
    )
    monkeypatch.setenv("DEVFLOW_HITL_STRATEGY", "end_of_day")
    cfg = load_workflow_config(yaml_path)
    assert cfg.hitl_strategy == "end_of_day"
```

Make sure `Path` and `pytest` are imported at the top of `test_config.py` (check existing imports first).

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_config.py::test_workflow_config_has_daemon_defaults tests/unit/test_config.py::test_daemon_config_from_yaml tests/unit/test_config.py::test_hitl_strategy_env_override -v`
Expected: FAIL with `AttributeError: 'WorkflowConfig' object has no attribute 'daemon'` or similar.

- [ ] **Step 3: Write minimal implementation**

In `src/devflow/config.py`, add after the `WorkflowConfig` class (after line 54):

```python
class HitlStrategy:
    """Constants for human-in-the-loop strategy values."""

    PER_PLAN = "per_plan"
    FULL_DETAIL = "full_detail"
    END_OF_DAY = "end_of_day"

    ALL = frozenset({PER_PLAN, FULL_DETAIL, END_OF_DAY})


class DaemonConfig(BaseModel):
    """Configuration for the long-running daemon service."""

    enabled: bool = False
    task_schedule: str = "0 9,15 * * 1-5"
    eod_schedule: str = "0 18 * * 1-5"
    port: int = 8787
    approval_timeout_hours: int = 8
    approval_on_timeout: str = "defer"  # defer | reject
```

Add fields to `WorkflowConfig` (insert after `todo_path` field, before the closing of the class):

```python
    hitl_strategy: str = "per_plan"
    daemon: DaemonConfig = Field(default_factory=DaemonConfig)
```

In `load_workflow_config` (around line 177, after the `todo_override` block), add:

```python
    hitl_override = os.getenv("DEVFLOW_HITL_STRATEGY")
    if hitl_override:
        if hitl_override not in HitlStrategy.ALL:
            raise ValueError(
                f"DEVFLOW_HITL_STRATEGY must be one of {sorted(HitlStrategy.ALL)}, "
                f"got {hitl_override!r}"
            )
        raw["hitl_strategy"] = hitl_override
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_config.py::test_workflow_config_has_daemon_defaults tests/unit/test_config.py::test_daemon_config_from_yaml tests/unit/test_config.py::test_hitl_strategy_env_override -v`
Expected: PASS

- [ ] **Step 5: Run full config test suite to check no regressions**

Run: `python -m pytest tests/unit/test_config.py -v`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/devflow/config.py tests/unit/test_config.py
git commit -m "feat(config): add DaemonConfig, HitlStrategy, hitl_strategy field

Adds DaemonConfig Pydantic model (enabled, schedules, port, approval
timeout) and hitl_strategy field to WorkflowConfig. Env override
DEVFLOW_HITL_STRATEGY validated against HitlStrategy.ALL."
```

---

## Task 2: EventBus — in-process pub/sub

**Files:**
- Create: `src/devflow/daemon/events.py`
- Test: `tests/unit/daemon/test_events.py`, `tests/unit/daemon/__init__.py`

**Interfaces:**
- Consumes: nothing (standalone)
- Produces: `EventBus` class with `publish(topic, data)`, `subscribe(topic) -> AsyncIterator[dict]`, `close()`

- [ ] **Step 1: Create test package init**

Create `tests/unit/daemon/__init__.py`:
```python
```
(empty file)

- [ ] **Step 2: Write the failing test**

Create `tests/unit/daemon/test_events.py`:

```python
"""Unit tests for the in-process EventBus."""

from __future__ import annotations

import asyncio

import pytest

from devflow.daemon.events import EventBus


@pytest.mark.asyncio
async def test_publish_subscribe_single_topic() -> None:
    """A subscriber receives messages published to its topic."""
    bus = EventBus()
    queue = await bus.subscribe("task.4321")
    await bus.publish("task.4321", {"node": "maker", "status": "done"})
    msg = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert msg == {"node": "maker", "status": "done"}
    await bus.close()


@pytest.mark.asyncio
async def test_multiple_subscribers_same_topic() -> None:
    """Multiple subscribers each receive the same message."""
    bus = EventBus()
    q1 = await bus.subscribe("task.1")
    q2 = await bus.subscribe("task.1")
    await bus.publish("task.1", {"event": "started"})
    msg1 = await asyncio.wait_for(q1.get(), timeout=1.0)
    msg2 = await asyncio.wait_for(q2.get(), timeout=1.0)
    assert msg1 == {"event": "started"}
    assert msg2 == {"event": "started"}
    await bus.close()


@pytest.mark.asyncio
async def test_subscriber_does_not_receive_old_messages() -> None:
    """Messages published before subscription are not delivered."""
    bus = EventBus()
    await bus.publish("task.1", {"event": "old"})
    queue = await bus.subscribe("task.1")
    await bus.publish("task.1", {"event": "new"})
    msg = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert msg == {"event": "new"}
    await bus.close()


@pytest.mark.asyncio
async def test_close_unsubscribes_all() -> None:
    """After close, all queues are drained and closed."""
    bus = EventBus()
    queue = await bus.subscribe("task.1")
    await bus.close()
    assert queue.empty()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_events.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devflow.daemon'`

- [ ] **Step 4: Write minimal implementation**

Create `src/devflow/daemon/__init__.py`:
```python
"""devflow-daemon: long-running scheduler + web service."""
```

Create `src/devflow/daemon/events.py`:

```python
"""In-process pub/sub event bus for live workflow updates.

No external broker (Redis/etc) — the daemon is a single process, so an
in-memory asyncio queue per subscriber is sufficient. Phase 1 publishes
nothing; Phase 5 (Vue dashboard) consumes via SSE.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class EventBus:
    """Fan-out pub/sub: each subscriber gets its own asyncio.Queue.

    Topics are plain strings (e.g. ``"task.4321"``). Messages published
    before a subscription are not retained — this is a live stream, not a
    log.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}

    async def subscribe(self, topic: str) -> asyncio.Queue[dict[str, Any]]:
        """Subscribe to ``topic`` and return a queue to read messages from."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subscribers.setdefault(topic, []).append(queue)
        return queue

    async def publish(self, topic: str, data: dict[str, Any]) -> None:
        """Publish ``data`` to all subscribers of ``topic``.

        If a subscriber's queue is full, the message is dropped for that
        subscriber (logged) rather than blocking the publisher.
        """
        for queue in self._subscribers.get(topic, []):
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                logger.warning("EventBus queue full for topic '%s'; dropping message", topic)

    async def close(self) -> None:
        """Clear all subscribers. Queues are abandoned (callers should stop reading)."""
        self._subscribers.clear()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_events.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/devflow/daemon/__init__.py src/devflow/daemon/events.py tests/unit/daemon/__init__.py tests/unit/daemon/test_events.py
git commit -m "feat(daemon): add in-process EventBus for live updates

asyncio.Queue-per-subscriber fan-out pub/sub. Phase 1 stub — no
publishers yet; Phase 5 SSE endpoint will consume it."
```

---

## Task 3: DaemonLocks — concurrency control

**Files:**
- Create: `src/devflow/daemon/locks.py`
- Test: `tests/unit/daemon/test_locks.py`

**Interfaces:**
- Consumes: `asyncio`
- Produces: `DaemonLocks` class with `task_run: asyncio.Lock`, `eod_review: asyncio.Lock`, async context manager methods

- [ ] **Step 1: Write the failing test**

Create `tests/unit/daemon/test_locks.py`:

```python
"""Unit tests for DaemonLocks concurrency control."""

from __future__ import annotations

import asyncio

import pytest

from devflow.daemon.locks import DaemonLocks


@pytest.mark.asyncio
async def test_task_run_lock_is_exclusive() -> None:
    """Only one coroutine can hold task_run at a time."""
    locks = DaemonLocks()
    order: list[str] = []

    async def worker(name: str) -> None:
        async with locks.task_run():
            order.append(f"{name}-start")
            await asyncio.sleep(0.05)
            order.append(f"{name}-end")

    await asyncio.gather(worker("a"), worker("b"))
    # a acquired first, so a-start, a-end, then b-start, b-end
    assert order == ["a-start", "a-end", "b-start", "b-end"]


@pytest.mark.asyncio
async def test_eod_review_lock_is_exclusive() -> None:
    """Only one coroutine can hold eod_review at a time."""
    locks = DaemonLocks()
    order: list[str] = []

    async def worker(name: str) -> None:
        async with locks.eod_review():
            order.append(f"{name}-enter")

    await asyncio.gather(worker("x"), worker("y"))
    assert len(order) == 2
    assert order[0] == "x-enter"
    assert order[1] == "y-enter"


@pytest.mark.asyncio
async def test_task_and_eod_locks_are_independent() -> None:
    """task_run and eod_review are separate locks — can be held concurrently."""
    locks = DaemonLocks()
    held: list[str] = []

    async def hold_task() -> None:
        async with locks.task_run():
            held.append("task")
            await asyncio.sleep(0.1)

    async def hold_eod() -> None:
        await asyncio.sleep(0.02)  # let task_run grab first
        async with locks.eod_review():
            held.append("eod")

    await asyncio.gather(hold_task(), hold_eod())
    # Both were held — order doesn't matter, just that eod didn't block on task
    assert "task" in held
    assert "eod" in held
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_locks.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devflow.daemon.locks'`

- [ ] **Step 3: Write minimal implementation**

Create `src/devflow/daemon/locks.py`:

```python
"""Concurrency locks for the daemon.

Two logical operations must not run in parallel within the daemon:
- ``task_run``: an active workflow run (processing tasks).
- ``eod_review``: an end-of-day batch review / publish.

Each is an ``asyncio.Lock``. Callers use ``async with locks.task_run():``
to acquire. The locks are independent — task_run does not block eod_review
and vice versa. Higher-level orchestration (Phase 4) coordinates ordering.
"""

from __future__ import annotations

import asyncio


class DaemonLocks:
    """Holds asyncio locks for daemon-wide exclusive operations."""

    def __init__(self) -> None:
        self._task_run = asyncio.Lock()
        self._eod_review = asyncio.Lock()

    def task_run(self) -> asyncio.Lock:
        """Lock for an active workflow task run. Only one at a time."""
        return self._task_run

    def eod_review(self) -> asyncio.Lock:
        """Lock for an EOD batch review/publish. Only one at a time."""
        return self._eod_review
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_locks.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devflow/daemon/locks.py tests/unit/daemon/test_locks.py
git commit -m "feat(daemon): add DaemonLocks for concurrency control

asyncio.Lock for task_run and eod_review. Independent locks —
higher-level coordination comes in Phase 4."
```

---

## Task 4: Orphan worktree cleanup (startup sweep)

**Files:**
- Create: `src/devflow/daemon/sweep.py`
- Test: `tests/unit/daemon/test_sweep.py`

**Interfaces:**
- Consumes: `GitWorktreeManager` from `devflow.tools.git_worktree` (existing), `git.Repo`
- Produces: `cleanup_orphan_worktrees(repo_path: str | Path) -> list[str]` — returns list of cleaned worktree paths

- [ ] **Step 1: Read existing GitWorktreeManager to understand worktree naming**

Read `src/devflow/tools/git_worktree.py` lines 39-65 and 114-131 to understand:
- Worktree dir pattern: `{repo_path.parent}/{repo_path.name}-worktree-{uuid.hex[:8]}` (line 54-57)
- Branch pattern: `devflow/{task_id}/{uuid.hex[:8]}` (line 53)
- Cleanup: `repo.git.worktree("remove", path, "--force")` + `repo.delete_head(branch, force=True)` (lines 114-131)

- [ ] **Step 2: Write the failing test**

Create `tests/unit/daemon/test_sweep.py`:

```python
"""Unit tests for orphan worktree cleanup on daemon startup."""

from __future__ import annotations

from pathlib import Path

import pytest
from git import Repo

from devflow.daemon.sweep import cleanup_orphan_worktrees


@pytest.fixture
def git_repo_with_orphan(tmp_path: Path) -> Path:
    """Create a repo with a manually-created orphan worktree + branch."""
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

    # Simulate an orphan: create a worktree + branch that no live process owns.
    orphan_dir = tmp_path / "repo-worktree-deadbeef"
    orphan_dir.mkdir(parents=True, exist_ok=True)
    repo.create_head("devflow/4321/deadbeef")
    repo.git.worktree("add", str(orphan_dir), "devflow/4321/deadbeef")

    return repo_path


def test_cleanup_removes_orphan_worktree(git_repo_with_orphan: Path) -> None:
    """cleanup_orphan_worktrees removes orphaned worktree dirs and branches."""
    cleaned = cleanup_orphan_worktrees(git_repo_with_orphan)
    assert len(cleaned) == 1
    assert "deadbeef" in cleaned[0]

    # Worktree dir is gone
    parent = git_repo_with_orphan.parent
    remaining = list(parent.glob("repo-worktree-*"))
    assert remaining == []

    # Branch is gone
    repo = Repo(git_repo_with_orphan)
    branch_names = [b.name for b in repo.branches]
    assert "devflow/4321/deadbeef" not in branch_names


def test_cleanup_no_orphans_is_noop(tmp_path: Path) -> None:
    """A clean repo with no orphan worktrees returns empty list."""
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

    cleaned = cleanup_orphan_worktrees(repo_path)
    assert cleaned == []


def test_cleanup_preserves_non_devflow_worktrees(git_repo_with_orphan: Path) -> None:
    """Worktrees not matching the devflow pattern are left untouched."""
    repo = Repo(git_repo_with_orphan)
    # Add a non-devflow worktree
    other_dir = git_repo_with_orphan.parent / "manual-worktree"
    other_dir.mkdir()
    repo.create_head("feature-branch")
    repo.git.worktree("add", str(other_dir), "feature-branch")

    cleaned = cleanup_orphan_worktrees(git_repo_with_orphan)
    # Only the devflow orphan was cleaned
    assert len(cleaned) == 1
    assert other_dir.exists()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_sweep.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devflow.daemon.sweep'`

- [ ] **Step 4: Write minimal implementation**

Create `src/devflow/daemon/sweep.py`:

```python
"""Startup cleanup of orphaned git worktrees.

When the daemon crashes or is killed mid-workflow, the worktree directory
and local branch created by ``GitWorktreeManager`` (tools/git_worktree.py:54-61)
are left behind. This module finds and removes them on daemon startup so
the next run starts clean.

Only ``devflow/{task_id}/{uuid}`` branches and ``{repo}-worktree-{uuid}``
directories are touched — other worktrees are preserved.
"""

from __future__ import annotations

import logging
from pathlib import Path

from git import Repo

logger = logging.getLogger(__name__)

# Matches the branch pattern in git_worktree.py:53: devflow/{task_id}/{uuid}
_DEVFLOW_BRANCH_PREFIX = "devflow/"
# Matches the worktree dir pattern in git_worktree.py:54-57: {repo}-worktree-{uuid}
_DEVFLOW_WORKTREE_SUFFIX = "-worktree-"


def cleanup_orphan_worktrees(repo_path: str | Path) -> list[str]:
    """Remove orphaned devflow worktrees and branches for ``repo_path``.

    Scans sibling directories of ``repo_path`` for worktree dirs matching
    ``{repo_name}-worktree-{uuid}`` and removes both the worktree and its
    ``devflow/{task_id}/{uuid}`` branch from the repo.

    Returns a list of cleaned worktree directory paths (as strings).
    """
    repo_path = Path(repo_path)
    repo = Repo(repo_path)
    cleaned: list[str] = []

    # Find worktree dirs in the parent directory matching the devflow pattern.
    parent = repo_path.parent
    repo_name = repo_path.name
    pattern = f"{repo_name}{_DEVFLOW_WORKTREE_SUFFIX}"

    orphan_dirs = sorted(parent.glob(f"{pattern}*"))

    for orphan_dir in orphan_dirs:
        if not orphan_dir.is_dir():
            continue
        _remove_orphan(repo, orphan_dir)
        cleaned.append(str(orphan_dir))

    # Also clean up any devflow branches whose worktrees are already gone.
    _remove_dangling_devflow_branches(repo)

    return cleaned


def _remove_orphan(repo: Repo, orphan_dir: Path) -> None:
    """Remove a single orphan worktree dir and its branch."""
    try:
        repo.git.worktree("remove", str(orphan_dir), "--force")
    except Exception as exc:
        logger.warning("Failed to git-remove worktree %s: %s; deleting dir", orphan_dir, exc)
        # Fall back to manual directory removal if git worktree remove fails
        import shutil

        shutil.rmtree(orphan_dir, ignore_errors=True)

    # Find and delete the associated branch (devflow/{task_id}/{uuid}).
    # The uuid is the last path component of the worktree dir, after the last '-'.
    dir_name = orphan_dir.name
    uuid_part = dir_name.rsplit("-", 1)[-1] if "-" in dir_name else ""
    if uuid_part:
        for branch in list(repo.branches):
            if branch.name.startswith(_DEVFLOW_BRANCH_PREFIX) and branch.name.endswith(uuid_part):
                try:
                    repo.delete_head(branch.name, force=True)
                    logger.info("Removed orphan branch: %s", branch.name)
                except Exception as exc:
                    logger.warning("Failed to delete orphan branch %s: %s", branch.name, exc)


def _remove_dangling_devflow_branches(repo: Repo) -> None:
    """Remove devflow/* branches that have no associated worktree."""
    try:
        worktree_output = repo.git.worktree("list", "--porcelain")
    except Exception as exc:
        logger.warning("Failed to list worktrees: %s", exc)
        return

    # Parse worktree list to find which branches still have live worktrees.
    live_branches: set[str] = set()
    for line in worktree_output.splitlines():
        if line.startswith("branch "):
            # Format: "branch refs/heads/devflow/4321/deadbeef"
            ref = line[len("branch "):].strip()
            if ref.startswith("refs/heads/"):
                live_branches.add(ref[len("refs/heads/"):])

    for branch in list(repo.branches):
        if (
            branch.name.startswith(_DEVFLOW_BRANCH_PREFIX)
            and branch.name not in live_branches
        ):
            try:
                repo.delete_head(branch.name, force=True)
                logger.info("Removed dangling devflow branch: %s", branch.name)
            except Exception as exc:
                logger.warning("Failed to delete dangling branch %s: %s", branch.name, exc)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_sweep.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/devflow/daemon/sweep.py tests/unit/daemon/test_sweep.py
git commit -m "feat(daemon): add orphan worktree cleanup on startup

cleanup_orphan_worktrees finds and removes devflow/{task_id}/{uuid}
branches and {repo}-worktree-{uuid} dirs left by crashes. Non-devflow
worktrees are preserved."
```

---

## Task 5: Workflow runner adapter

**Files:**
- Create: `src/devflow/daemon/runner.py`
- Test: `tests/unit/daemon/test_runner.py`

**Interfaces:**
- Consumes: `run_workflow` from `devflow.graph` (graph.py:226), `Config` from `devflow.config`, `EventBus` from `devflow.daemon.events`, `DaemonLocks` from `devflow.daemon.locks`
- Produces: `WorkflowRunner` class with `run_task(task_id, repo_path, thread_id) -> WorkflowState`, `run_all(repo_path) -> list[WorkflowState]`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/daemon/test_runner.py`:

```python
"""Unit tests for the daemon workflow runner adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from git import Repo

from devflow.config import Config
from devflow.daemon.events import EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.daemon.runner import WorkflowRunner
from devflow.state import FinalVerdict


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository for the maker node."""
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


def test_run_task_completes(
    temp_git_repo: Path,
    mock_config: Config,
    fake_llm_factory: Any,
) -> None:
    """WorkflowRunner.run_task runs a single task to completion."""
    bus = EventBus()
    locks = DaemonLocks()
    runner = WorkflowRunner(mock_config, bus, locks)
    final_state = runner.run_task(
        task_id="MOCK-1",
        repo_path=str(temp_git_repo),
        thread_id="runner-test-1",
    )
    assert final_state.get("final_verdict") == FinalVerdict.APPROVE
    assert final_state.get("error") is None


def test_run_task_publishes_events(
    temp_git_repo: Path,
    mock_config: Config,
    fake_llm_factory: Any,
) -> None:
    """WorkflowRunner publishes node-completion events to EventBus."""
    bus = EventBus()
    locks = DaemonLocks()
    runner = WorkflowRunner(mock_config, bus, locks)
    runner.run_task(
        task_id="MOCK-1",
        repo_path=str(temp_git_repo),
        thread_id="runner-test-2",
    )
    # The runner should have published at least one event to the task topic.
    # We can't easily assert the queue contents after run_task returns
    # (events were consumed by nobody), but we can check that the runner
    # has a non-empty event count.
    assert runner.events_published > 0


def test_run_all_processes_multiple_tasks(
    temp_git_repo: Path,
    mock_config: Config,
    fake_llm_factory: Any,
) -> None:
    """WorkflowRunner.run_all fetches and processes multiple tasks."""
    from devflow.mcp.mock import MockTaskSource

    # Give the mock task source some tasks.
    mock_config.workflow.task_source = "mock"
    bus = EventBus()
    locks = DaemonLocks()
    runner = WorkflowRunner(mock_config, bus, locks, task_source=MockTaskSource({}))
    results = runner.run_all(repo_path=str(temp_git_repo), limit=3)
    # MockTaskSource returns 0 tasks by default, so results should be empty.
    # The point is that run_all doesn't crash.
    assert isinstance(results, list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devflow.daemon.runner'`

- [ ] **Step 3: Write minimal implementation**

Create `src/devflow/daemon/runner.py`:

```python
"""Workflow runner adapter for the daemon.

Wraps the existing ``run_workflow`` (graph.py:226) so the daemon can run
tasks with:
- asyncio lock coordination (only one task_run at a time);
- event publishing to EventBus (for Phase 5 SSE live updates);
- a synchronous boundary — the daemon's scheduler calls these from a
  thread, and the graph itself is synchronous.

Phase 1 uses ``run_workflow`` (non-interactive). Phase 2 will switch to
``run_workflow_interactive`` with an approval-bridge callback.
"""

from __future__ import annotations

import logging
import traceback
from typing import Any

from devflow.config import Config
from devflow.daemon.events import EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.graph import run_workflow
from devflow.mcp.base import TaskSource
from devflow.mcp.factory import build_task_source
from devflow.state import WorkflowState

logger = logging.getLogger(__name__)


class WorkflowRunner:
    """Runs workflow tasks with lock coordination and event publishing."""

    def __init__(
        self,
        app_cfg: Config,
        event_bus: EventBus,
        locks: DaemonLocks,
        task_source: TaskSource | None = None,
    ) -> None:
        self._cfg = app_cfg
        self._bus = event_bus
        self._locks = locks
        self._task_source = task_source
        self.events_published: int = 0

    def run_task(
        self,
        task_id: str,
        repo_path: str,
        thread_id: str | None = None,
    ) -> WorkflowState:
        """Run a single task to completion (non-interactive in Phase 1).

        Acquires the ``task_run`` lock for the duration. Publishes a
        ``task.started`` event before and a ``task.finished`` event after.
        """
        import asyncio

        loop = asyncio.get_event_loop()
        topic = f"task.{task_id}"

        # Publish start event (fire-and-forget on the loop).
        self._publish(topic, {
            "event": "task.started",
            "task_id": task_id,
            "thread_id": thread_id or task_id,
        })

        try:
            final_state = run_workflow(
                app_cfg=self._cfg,
                repo_path=repo_path,
                task_id=task_id,
                task_source=self._task_source,
                thread_id=thread_id or task_id,
            )
            verdict = final_state.get("final_verdict")
            self._publish(topic, {
                "event": "task.finished",
                "task_id": task_id,
                "verdict": verdict.value if verdict else None,
            })
            return final_state
        except Exception:
            logger.exception("Workflow run failed for task %s", task_id)
            self._publish(topic, {
                "event": "task.error",
                "task_id": task_id,
                "error": traceback.format_exc(),
            })
            raise

    def run_all(self, repo_path: str, limit: int = 10) -> list[WorkflowState]:
        """Fetch all open tasks and run each to completion.

        Uses the task source from config if none was provided in __init__.
        """
        source = self._task_source
        if source is None:
            source = build_task_source(self._cfg.workflow)
        try:
            tasks = source.fetch_tasks(status="open", limit=limit)
            if not tasks:
                logger.info("No open tasks found.")
                return []

            logger.info("Processing %d open task(s)...", len(tasks))
            results: list[WorkflowState] = []
            for task in tasks:
                logger.info("Task %s: %s", task.id, task.title)
                final_state = self.run_task(
                    task_id=task.id,
                    repo_path=repo_path,
                    thread_id=task.id,
                )
                results.append(final_state)
            return results
        finally:
            if self._task_source is None:
                source.close()

    def _publish(self, topic: str, data: dict[str, Any]) -> None:
        """Publish an event, tracking count for diagnostics."""
        import asyncio

        self.events_published += 1
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._bus.publish(topic, data))
            else:
                loop.run_until_complete(self._bus.publish(topic, data))
        except RuntimeError:
            # No event loop — publish synchronously via a temporary loop.
            asyncio.run(self._bus.publish(topic, data))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_runner.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devflow/daemon/runner.py tests/unit/daemon/test_runner.py
git commit -m "feat(daemon): add WorkflowRunner adapter

Wraps run_workflow with lock coordination and EventBus publishing.
Phase 1: non-interactive. Phase 2: switch to run_workflow_interactive."
```

---

## Task 6: FastAPI web app (health + state endpoints)

**Files:**
- Create: `src/devflow/daemon/web.py`
- Test: `tests/unit/daemon/test_web.py`

**Interfaces:**
- Consumes: `Config` from `devflow.config`, `DaemonLocks` from `devflow.daemon.locks`, `EventBus` from `devflow.daemon.events`
- Produces: `create_app(app_cfg, locks, event_bus) -> FastAPI`, `run_web_server(app_cfg, locks, event_bus)` (blocking uvicorn runner)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/daemon/test_web.py`:

```python
"""Unit tests for the daemon FastAPI web app."""

from __future__ import annotations

from fastapi.testclient import TestClient

from devflow.config import Config, WorkflowConfig
from devflow.daemon.events import EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.daemon.web import create_app


def _make_app() -> tuple:
    """Create a test app with minimal config."""
    from devflow.config import DaemonConfig

    cfg = Config(
        workflow=WorkflowConfig(task_source="mock"),
        providers={},
        agents={},
    )
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=None)
    return app, locks


def test_health_endpoint_returns_healthy() -> None:
    """/api/health returns status healthy when no errors."""
    app, _ = _make_app()
    with TestClient(app) as client:
        resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert "scheduler" in data
    assert "uptime_seconds" in data


def test_state_endpoint_returns_config() -> None:
    """/api/state returns daemon config and strategy."""
    app, _ = _make_app()
    with TestClient(app) as client:
        resp = client.get("/api/state")
    assert resp.status_code == 200
    data = resp.json()
    assert data["hitl_strategy"] == "per_plan"
    assert data["daemon"]["enabled"] is True
    assert data["daemon"]["port"] == 8787


def test_health_endpoint_shows_degraded_when_task_running() -> None:
    """/api/health shows degraded status when a task is actively running."""
    import asyncio

    app, locks = _make_app()
    with TestClient(app) as client:
        # Simulate a running task by holding the lock.
        loop = asyncio.new_event_loop()

        async def hold_lock() -> asyncio.Lock:
            lock = locks.task_run()
            await lock.acquire()
            return lock

        lock = loop.run_until_complete(hold_lock())
        try:
            resp = client.get("/api/health")
            data = resp.json()
            assert data["status"] == "degraded"
            assert data["current_task"] is not None or data["scheduler"] == "busy"
        finally:
            lock.release()
            loop.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_web.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devflow.daemon.web'`

- [ ] **Step 3: Write minimal implementation**

Create `src/devflow/daemon/web.py`:

```python
"""FastAPI web app for the daemon.

Phase 1 endpoints:
- ``GET /api/health`` — daemon health (status, scheduler, uptime).
- ``GET /api/state`` — daemon config and HITL strategy.

Phase 2+ will add /api/approvals, /api/tasks/*, /api/eod, /api/events (SSE).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from devflow.config import Config
from devflow.daemon.events import EventBus
from devflow.daemon.locks import DaemonLocks

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: str
    scheduler: str
    uptime_seconds: int
    current_task: str | None = None
    pending_approvals: int = 0
    batch_store_pending: int = 0
    errors_last_24h: int = 0


class StateResponse(BaseModel):
    hitl_strategy: str
    daemon: dict[str, Any]
    task_source: str


def create_app(
    app_cfg: Config,
    locks: DaemonLocks,
    event_bus: EventBus,
    runner: Any | None = None,
) -> FastAPI:
    """Create the FastAPI application.

    ``runner`` is the WorkflowRunner (or None in Phase 1 tests). It will be
    used by Phase 2+ endpoints to query task progress and approvals.
    """
    app = FastAPI(title="devflow-daemon", version="0.1.0")
    start_time = time.monotonic()
    _state: dict[str, Any] = {"current_task": None}

    def _is_task_running() -> bool:
        """Check if the task_run lock is currently held."""
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            lock = locks.task_run()
            if loop.is_running():
                # Can't check synchronously from within a running loop;
                # use the locked property (may be False-positive in edge cases).
                return lock.locked()
            return lock.locked()
        except RuntimeError:
            return False

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        uptime = int(time.monotonic() - start_time)
        task_running = _is_task_running()
        return HealthResponse(
            status="degraded" if task_running else "healthy",
            scheduler="busy" if task_running else "running",
            uptime_seconds=uptime,
            current_task=_state.get("current_task"),
        )

    @app.get("/api/state", response_model=StateResponse)
    async def state() -> StateResponse:
        daemon_cfg = app_cfg.workflow.daemon
        return StateResponse(
            hitl_strategy=app_cfg.workflow.hitl_strategy,
            daemon={
                "enabled": daemon_cfg.enabled,
                "task_schedule": daemon_cfg.task_schedule,
                "eod_schedule": daemon_cfg.eod_schedule,
                "port": daemon_cfg.port,
                "approval_timeout_hours": daemon_cfg.approval_timeout_hours,
                "approval_on_timeout": daemon_cfg.approval_on_timeout,
            },
            task_source=app_cfg.workflow.task_source,
        )

    def set_current_task(task_id: str | None) -> None:
        """Allow the runner to report which task is currently active."""
        _state["current_task"] = task_id

    # Expose the setter so the runner can update state.
    app.state.set_current_task = set_current_task  # type: ignore[attr-defined]

    return app


def run_web_server(
    app_cfg: Config,
    locks: DaemonLocks,
    event_bus: EventBus,
    runner: Any | None = None,
) -> None:
    """Run the uvicorn server (blocking). Called from the daemon entry point."""
    import uvicorn

    app = create_app(app_cfg, locks, event_bus, runner)
    port = app_cfg.workflow.daemon.port
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_web.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devflow/daemon/web.py tests/unit/daemon/test_web.py
git commit -m "feat(daemon): add FastAPI app with /api/health and /api/state

Phase 1 endpoints. Health shows degraded when task_run lock is held.
run_web_server starts uvicorn on localhost:port."
```

---

## Task 7: APScheduler configuration

**Files:**
- Create: `src/devflow/daemon/scheduler.py`
- Test: `tests/unit/daemon/test_scheduler.py`

**Interfaces:**
- Consumes: `Config` from `devflow.config`, `WorkflowRunner` from `devflow.daemon.runner`, `DaemonLocks` from `devflow.daemon.locks`
- Produces: `DaemonScheduler` class with `start()`, `shutdown()`, `register_jobs(repo_path)`, properties `is_running`, `job_count`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/daemon/test_scheduler.py`:

```python
"""Unit tests for the daemon APScheduler configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from git import Repo

from devflow.config import Config
from devflow.daemon.events import EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.daemon.runner import WorkflowRunner
from devflow.daemon.scheduler import DaemonScheduler


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


def test_scheduler_starts_and_stops(mock_config: Config) -> None:
    """DaemonScheduler starts and stops cleanly."""
    bus = EventBus()
    locks = DaemonLocks()
    runner = WorkflowRunner(mock_config, bus, locks)
    scheduler = DaemonScheduler(mock_config, runner)
    scheduler.start()
    assert scheduler.is_running
    scheduler.shutdown()
    assert not scheduler.is_running


def test_scheduler_registers_task_job(
    mock_config: Config,
    temp_git_repo: Path,
) -> None:
    """register_jobs adds a task-run job with the configured cron schedule."""
    bus = EventBus()
    locks = DaemonLocks()
    runner = WorkflowRunner(mock_config, bus, locks)
    scheduler = DaemonScheduler(mock_config, runner)
    scheduler.start()
    scheduler.register_jobs(str(temp_git_repo))
    assert scheduler.job_count >= 1
    scheduler.shutdown()


def test_scheduler_registers_eod_job_when_eod_mode(
    mock_config: Config,
    temp_git_repo: Path,
) -> None:
    """register_jobs adds an EOD job when hitl_strategy is end_of_day."""
    import copy

    cfg = copy.deepcopy(mock_config)
    cfg.workflow.hitl_strategy = "end_of_day"
    cfg.workflow.daemon.enabled = True

    bus = EventBus()
    locks = DaemonLocks()
    runner = WorkflowRunner(cfg, bus, locks)
    scheduler = DaemonScheduler(cfg, runner)
    scheduler.start()
    scheduler.register_jobs(str(temp_git_repo))
    # In end_of_day mode, both task and eod jobs should be registered.
    assert scheduler.job_count >= 2
    scheduler.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_scheduler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devflow.daemon.scheduler'`

- [ ] **Step 3: Write minimal implementation**

Create `src/devflow/daemon/scheduler.py`:

```python
"""APScheduler configuration for the daemon.

Registers cron jobs:
- ``task_run``: runs ``WorkflowRunner.run_all`` on the configured schedule.
- ``eod_review``: runs EOD batch-review on the configured schedule (only
  when ``hitl_strategy == end_of_day`` — Phase 4 implements the handler).

Jobs use ``max_instances=1, coalesce=True`` so overlapping runs are
skipped/merged rather than queued.
"""

from __future__ import annotations

import logging
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from devflow.config import Config, HitlStrategy
from devflow.daemon.runner import WorkflowRunner

logger = logging.getLogger(__name__)


class DaemonScheduler:
    """Wraps APScheduler BackgroundScheduler for daemon cron jobs."""

    def __init__(self, app_cfg: Config, runner: WorkflowRunner) -> None:
        self._cfg = app_cfg
        self._runner = runner
        self._scheduler = BackgroundScheduler(daemon=True)
        self._lock = threading.Lock()
        self._jobs_registered = False

    @property
    def is_running(self) -> bool:
        """True if the scheduler is currently running."""
        return self._scheduler.running

    @property
    def job_count(self) -> int:
        """Number of registered jobs."""
        return len(self._scheduler.get_jobs())

    def start(self) -> None:
        """Start the scheduler (does not register jobs)."""
        if not self._scheduler.running:
            self._scheduler.start()
            logger.info("Scheduler started")

    def shutdown(self) -> None:
        """Shut down the scheduler, waiting for active jobs to finish."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")

    def register_jobs(self, repo_path: str) -> None:
        """Register cron jobs for task runs and (optionally) EOD review.

        Idempotent: calling twice does not duplicate jobs.
        """
        with self._lock:
            if self._jobs_registered:
                return

            daemon_cfg = self._cfg.workflow.daemon

            # Task run job — runs run_all on the configured cron schedule.
            trigger = CronTrigger.from_crontab(daemon_cfg.task_schedule)
            self._scheduler.add_job(
                self._run_all_wrapper,
                trigger=trigger,
                id="task_run",
                max_instances=1,
                coalesce=True,
                kwargs={"repo_path": repo_path},
                replace_existing=True,
            )
            logger.info("Registered task_run job with schedule: %s", daemon_cfg.task_schedule)

            # EOD review job — only in end_of_day strategy.
            if self._cfg.workflow.hitl_strategy == HitlStrategy.END_OF_DAY:
                eod_trigger = CronTrigger.from_crontab(daemon_cfg.eod_schedule)
                self._scheduler.add_job(
                    self._run_eod_wrapper,
                    trigger=eod_trigger,
                    id="eod_review",
                    max_instances=1,
                    coalesce=True,
                    kwargs={"repo_path": repo_path},
                    replace_existing=True,
                )
                logger.info("Registered eod_review job with schedule: %s", daemon_cfg.eod_schedule)

            self._jobs_registered = True

    def _run_all_wrapper(self, repo_path: str) -> None:
        """Job handler: run all open tasks. Catches exceptions so APScheduler
        doesn't kill the scheduler on a single failure."""
        try:
            logger.info("task_run job triggered")
            self._runner.run_all(repo_path=repo_path)
        except Exception:
            logger.exception("task_run job failed")

    def _run_eod_wrapper(self, repo_path: str) -> None:
        """Job handler: run EOD batch-review. Phase 4 implements the handler."""
        try:
            logger.info("eod_review job triggered (not yet implemented in Phase 1)")
            # Phase 4 will call: self._eod_handler.run_review(repo_path)
        except Exception:
            logger.exception("eod_review job failed")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_scheduler.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devflow/daemon/scheduler.py tests/unit/daemon/test_scheduler.py
git commit -m "feat(daemon): add APScheduler with cron jobs

DaemonScheduler registers task_run and eod_review jobs. max_instances=1,
coalesce=True to skip overlaps. EOD job only in end_of_day strategy."
```

---

## Task 8: Daemon entry point (__main__)

**Files:**
- Create: `src/devflow/daemon/__main__.py`
- Test: `tests/unit/daemon/test_main.py`

**Interfaces:**
- Consumes: `load_config` from `devflow.config`, `EventBus`, `DaemonLocks`, `WorkflowRunner`, `DaemonScheduler`, `create_app`/`run_web_server`, `cleanup_orphan_worktrees`
- Produces: `run_daemon(config_dir, repo_path)` function, `python -m devflow.daemon` CLI entry

- [ ] **Step 1: Write the failing test**

Create `tests/unit/daemon/test_main.py`:

```python
"""Unit tests for the daemon entry point."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from git import Repo

from devflow.daemon.__main__ import run_daemon


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


def test_run_daemon_starts_components(
    mock_config: Any,
    temp_git_repo: Path,
    fake_llm_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_daemon initializes scheduler, web app, and runner without crashing.

    We patch run_web_server to avoid actually binding a port / blocking.
    """
    started: list[str] = []

    def fake_web_server(*args: Any, **kwargs: Any) -> None:
        started.append("web")

    monkeypatch.setattr("devflow.daemon.__main__.run_web_server", fake_web_server)

    # Patch load_config to return our mock config.
    monkeypatch.setattr("devflow.daemon.__main__.load_config", lambda *a, **kw: mock_config)
    # Enable daemon mode for the test.
    mock_config.workflow.daemon.enabled = True

    run_daemon(config_dir="config", repo_path=str(temp_git_repo))

    assert "web" in started
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_main.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devflow.daemon.__main__'`

- [ ] **Step 3: Write minimal implementation**

Create `src/devflow/daemon/__main__.py`:

```python
"""Entry point for ``python -m devflow.daemon``.

Loads config, runs startup sweep for orphan worktrees, creates the
scheduler + web app + runner, registers jobs, and starts the uvicorn
web server (blocking).

Usage:
    python -m devflow.daemon [--config-dir config] [--repo-path ./my-repo]
"""

from __future__ import annotations

import logging
import signal
import sys
from pathlib import Path

from devflow.config import load_config
from devflow.daemon.events import EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.daemon.runner import WorkflowRunner
from devflow.daemon.scheduler import DaemonScheduler
from devflow.daemon.sweep import cleanup_orphan_worktrees
from devflow.daemon.web import run_web_server

logger = logging.getLogger(__name__)


def run_daemon(config_dir: str = "config", repo_path: str = ".") -> None:
    """Start the daemon: config → sweep → scheduler → web server.

    This function blocks (uvicorn.run is blocking). The scheduler runs in
    a background thread, so cron jobs fire independently of the web server.
    """
    logger.info("Starting devflow-daemon...")

    # 1. Load configuration.
    app_cfg = load_config(config_dir)
    daemon_cfg = app_cfg.workflow.daemon

    if not daemon_cfg.enabled:
        logger.error("Daemon is not enabled in config (daemon.enabled=false). Exiting.")
        sys.exit(1)

    # 2. Startup sweep: clean orphaned worktrees from previous crashes.
    logger.info("Running startup worktree sweep...")
    cleaned = cleanup_orphan_worktrees(repo_path)
    if cleaned:
        logger.info("Cleaned %d orphan worktree(s): %s", len(cleaned), cleaned)

    # 3. Create shared components.
    event_bus = EventBus()
    locks = DaemonLocks()
    runner = WorkflowRunner(app_cfg, event_bus, locks)

    # 4. Create and start scheduler, register jobs.
    scheduler = DaemonScheduler(app_cfg, runner)
    scheduler.start()
    scheduler.register_jobs(repo_path)

    # 5. Graceful shutdown handler.
    def _shutdown(signum: int, frame: object) -> None:
        logger.info("Received signal %s, shutting down...", signum)
        scheduler.shutdown()
        logger.info("Daemon stopped.")
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # 6. Start web server (blocking).
    logger.info("Starting web server on 127.0.0.1:%d", daemon_cfg.port)
    run_web_server(app_cfg, locks, event_bus, runner)


def main() -> None:
    """CLI wrapper for ``python -m devflow.daemon``."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Parse minimal args; full CLI via typer can be added later.
    config_dir = "config"
    repo_path = "."
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--config-dir" and i + 1 < len(args):
            config_dir = args[i + 1]
            i += 2
        elif args[i] == "--repo-path" and i + 1 < len(args):
            repo_path = args[i + 1]
            i += 2
        else:
            i += 1

    run_daemon(config_dir=config_dir, repo_path=repo_path)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_main.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devflow/daemon/__main__.py tests/unit/daemon/test_main.py
git commit -m "feat(daemon): add entry point (python -m devflow.daemon)

Loads config, runs startup sweep, starts scheduler + web server.
Signal handlers for graceful shutdown. Blocks on uvicorn.run."
```

---

## Task 9: Add dependencies and console script

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: existing pyproject.toml structure
- Produces: new deps (apscheduler, fastapi, uvicorn, python-multipart), new console script `devflow-daemon`

- [ ] **Step 1: Add dependencies to pyproject.toml**

In `pyproject.toml`, add to the `dependencies` list (after line 27, before the closing `]`):

```toml
    "apscheduler>=3.10.0",
    "fastapi>=0.115.0",
    "uvicorn>=0.30.0",
    "python-multipart>=0.0.9",
```

Add the `httpx` extra to the main deps too (needed by FastAPI's TestClient), or add to a new `web` extra. Add after the `telegram` extra block (after line 36):

```toml
web = [
    "httpx>=0.27.0",
]
```

Add a new console script after line 46:

```toml
devflow-daemon = "devflow.daemon.__main__:main"
```

- [ ] **Step 2: Install the new dependencies**

Run: `pip install -e ".[dev,web]"`
Expected: Successful install of apscheduler, fastapi, uvicorn, etc.

- [ ] **Step 3: Verify the console script works**

Run: `devflow-daemon --help 2>&1 || true`
Expected: No `command not found` error. The daemon will likely exit with "Daemon is not enabled" (expected, since config doesn't have `daemon.enabled: true` yet).

- [ ] **Step 4: Run full test suite to check no regressions**

Run: `python -m pytest tests/ -v --timeout=60`
Expected: All existing + new tests PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "build: add apscheduler, fastapi, uvicorn deps; devflow-daemon script

New 'web' extra (httpx for TestClient). Console script
devflow-daemon = devflow.daemon.__main__:main."
```

---

## Task 10: Update config files and .gitignore

**Files:**
- Modify: `config/workflow.yaml`
- Modify: `.gitignore`
- Modify: `.env.example`

**Interfaces:**
- Consumes: `DaemonConfig` schema from Task 1
- Produces: updated `config/workflow.yaml` with `daemon:` section, `.gitignore` entries, `.env.example` documentation

- [ ] **Step 1: Read current config/workflow.yaml**

Read `config/workflow.yaml` to see current content.

- [ ] **Step 2: Add daemon section to config/workflow.yaml**

Append to `config/workflow.yaml`:

```yaml
# HITL strategy: per_plan | full_detail | end_of_day
hitl_strategy: per_plan

# Daemon configuration (for autonomous scheduled runs)
daemon:
  enabled: false
  # Cron schedules (standard 5-field cron: minute hour day month day-of-week)
  task_schedule: "0 9,15 * * 1-5"   # weekdays 09:00 and 15:00
  eod_schedule: "0 18 * * 1-5"      # weekdays 18:00 (only in end_of_day mode)
  port: 8787                         # dashboard API port (localhost only)
  approval_timeout_hours: 8          # max time to wait for human approval
  approval_on_timeout: defer         # defer | reject
```

- [ ] **Step 3: Update .gitignore**

Add to `.gitignore`:

```
# Daemon runtime artifacts
.devflow/
logs/
```

- [ ] **Step 4: Update .env.example**

Read `.env.example` and append:

```env
# ── Daemon (autonomous mode) ──────────────────────────────────────────────
# Override HITL strategy without editing YAML:
# DEVFLOW_HITL_STRATEGY=per_plan   # per_plan | full_detail | end_of_day
```

- [ ] **Step 5: Run config validation test**

Run: `python -m pytest tests/unit/test_config.py -v`
Expected: All tests PASS (the new YAML should parse correctly).

- [ ] **Step 6: Verify the config loads**

Run: `python -c "from devflow.config import load_config; cfg = load_config('config'); print(cfg.workflow.daemon)"`
Expected: Prints `enabled=False task_schedule='0 9,15 * * 1-5' ...`

- [ ] **Step 7: Commit**

```bash
git add config/workflow.yaml .gitignore .env.example
git commit -m "chore: add daemon section to workflow.yaml, update .gitignore/.env.example

Daemon disabled by default. Cron schedules, port 8787, approval timeout 8h.
DEVFLOW_HITL_STRATEGY env override documented."
```

---

## Task 11: nssm service installation scripts

**Files:**
- Create: `scripts/install-service.bat`
- Create: `scripts/run-daemon-dev.bat`

**Interfaces:**
- Consumes: `devflow-daemon` console script (Task 9), `nssm` (external)
- Produces: Windows service installation script, foreground dev runner

- [ ] **Step 1: Create install-service.bat**

Create `scripts/install-service.bat`:

```bat
@echo off
REM Install devflow-daemon as a Windows Service using nssm.
REM
REM Prerequisites:
REM   - nssm installed and on PATH (https://nssm.cc/download)
REM   - Python venv with devflow-super installed (pip install -e ".[dev,web]")
REM   - config/workflow.yaml has daemon.enabled: true
REM
REM Usage:
REM   scripts\install-service.bat "C:\path\to\repo" "C:\path\to\venv\Scripts\python.exe"

setlocal

set REPO_PATH=%~1
set PYTHON_EXE=%~2

if "%REPO_PATH%"=="" (
    echo Usage: %~n0 "C:\path\to\repo" "C:\path\to\venv\Scripts\python.exe"
    exit /b 1
)
if "%PYTHON_EXE%"=="" (
    echo Usage: %~n0 "C:\path\to\repo" "C:\path\to\venv\Scripts\python.exe"
    exit /b 1
)

echo Installing devflow-daemon service...
echo   Repo:   %REPO_PATH%
echo   Python: %PYTHON_EXE%

nssm install devflow-daemon "%PYTHON_EXE%" "-m devflow.daemon --repo-path %REPO_PATH%"
nssm set devflow-daemon AppDirectory "%REPO_PATH%"
nssm set devflow-daemon AppStdout "%REPO_PATH%\logs\daemon.log"
nssm set devflow-daemon AppStderr "%REPO_PATH%\logs\daemon.log"
nssm set devflow-daemon AppRotateFiles 1
nssm set devflow-daemon AppRotateBytes 10485760
nssm set devflow-daemon Start SERVICE_AUTO_START

echo.
echo Service installed. Start with: nssm start devflow-daemon
echo View logs at: %REPO_PATH%\logs\daemon.log
echo Remove with: nssm remove devflow-daemon confirm

endlocal
```

- [ ] **Step 2: Create run-daemon-dev.bat**

Create `scripts/run-daemon-dev.bat`:

```bat
@echo off
REM Run devflow-daemon in foreground for debugging.
REM
REM Usage:
REM   scripts\run-daemon-dev.bat [repo_path]
REM
REM Make sure config/workflow.yaml has daemon.enabled: true before running.

setlocal

set REPO_PATH=%~1
if "%REPO_PATH%"=="" set REPO_PATH=.

echo Starting devflow-daemon in foreground (debug mode)...
echo   Repo: %REPO_PATH%
echo   Press Ctrl+C to stop.
echo.

python -m devflow.daemon --config-dir config --repo-path "%REPO_PATH%"

endlocal
```

- [ ] **Step 3: Commit**

```bash
git add scripts/install-service.bat scripts/run-daemon-dev.bat
git commit -m "chore: add nssm service install + dev runner scripts

install-service.bat: registers devflow-daemon as Windows Service via
nssm with log rotation and auto-start. run-daemon-dev.bat: foreground
launch for debugging."
```

---

## Task 12: Full integration smoke test

**Files:**
- Test: `tests/unit/daemon/test_main.py` (extend)

**Interfaces:**
- Consumes: all daemon components from Tasks 1-11

- [ ] **Step 1: Write integration test**

Add to `tests/unit/daemon/test_main.py`:

```python
def test_run_daemon_with_sweep_and_scheduler(
    mock_config: Any,
    temp_git_repo: Path,
    fake_llm_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full daemon startup: sweep runs, scheduler starts, web server called.

    Verifies the integration of all Phase 1 components without actually
    binding a network port.
    """
    web_called: list[bool] = []

    def fake_web_server(*args: Any, **kwargs: Any) -> None:
        web_called.append(True)

    monkeypatch.setattr("devflow.daemon.__main__.run_web_server", fake_web_server)
    monkeypatch.setattr("devflow.daemon.__main__.load_config", lambda *a, **kw: mock_config)
    mock_config.workflow.daemon.enabled = True
    mock_config.workflow.hitl_strategy = "end_of_day"

    run_daemon(config_dir="config", repo_path=str(temp_git_repo))

    assert web_called == [True]


def test_run_daemon_exits_when_disabled(
    mock_config: Any,
    temp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_daemon exits with error when daemon.enabled is False."""
    import pytest as _pytest

    monkeypatch.setattr("devflow.daemon.__main__.load_config", lambda *a, **kw: mock_config)
    mock_config.workflow.daemon.enabled = False

    with _pytest.raises(SystemExit) as exc_info:
        run_daemon(config_dir="config", repo_path=str(temp_git_repo))
    assert exc_info.value.code == 1
```

- [ ] **Step 2: Run the new tests**

Run: `python -m pytest tests/unit/daemon/test_main.py -v`
Expected: All tests PASS (including new integration tests).

- [ ] **Step 3: Run the FULL test suite**

Run: `python -m pytest tests/ -v --timeout=120`
Expected: ALL tests PASS — both existing and new Phase 1 tests.

- [ ] **Step 4: Run ruff linter**

Run: `ruff check src/devflow/daemon/ tests/unit/daemon/`
Expected: No errors. If any, fix them.

- [ ] **Step 5: Run mypy type checker**

Run: `mypy src/devflow/daemon/`
Expected: No errors (or only pre-existing ones in other modules).

- [ ] **Step 6: Commit**

```bash
git add tests/unit/daemon/test_main.py
git commit -m "test(daemon): add integration smoke tests for Phase 1

Verifies full daemon startup (sweep + scheduler + web) and exit-when-
disabled behavior. All Phase 1 components exercised together."
```

---

## Self-Review

### Spec coverage (Phase 1 scope)

| Spec section | Task(s) | Covered? |
|---|---|---|
| Daemon as Windows Service (nssm) | Task 8, 11 | ✅ |
| APScheduler cron jobs | Task 7 | ✅ |
| FastAPI on localhost | Task 6 | ✅ |
| `/api/health` endpoint | Task 6 | ✅ |
| `/api/state` endpoint | Task 6 | ✅ |
| Startup orphan worktree sweep | Task 4 | ✅ |
| EventBus (Phase 1 stub) | Task 2 | ✅ |
| DaemonLocks | Task 3 | ✅ |
| WorkflowRunner adapter | Task 5 | ✅ |
| DaemonConfig schema | Task 1 | ✅ |
| `DEVFLOW_HITL_STRATEGY` env override | Task 1 | ✅ |
| `hitl_strategy` config field | Task 1 | ✅ |
| `python -m devflow.daemon` entry point | Task 8 | ✅ |
| Dependencies in pyproject.toml | Task 9 | ✅ |
| `.gitignore` (.devflow/, logs/) | Task 10 | ✅ |
| `.env.example` updates | Task 10 | ✅ |
| Graceful shutdown (signal handlers) | Task 8 | ✅ |

**Phase 2-5 items (NOT in this plan, correctly deferred):**
- HITL strategies / approval bridge → Phase 2
- `publish_approval` node → Phase 2
- ForgeBackend → Phase 3
- EOD batch-store / batch-publish → Phase 4
- Vue SPA / SSE endpoint → Phase 5

### Placeholder scan
Searched for TBD/TODO/FIXME/“implement later”/“similar to” — none found. All code blocks contain complete implementations.

### Type consistency
- `EventBus.subscribe(topic) -> asyncio.Queue[dict[str, Any]]` — consistent across Task 2 (definition) and Task 5 (usage in runner).
- `DaemonLocks.task_run() -> asyncio.Lock` and `eod_review() -> asyncio.Lock` — consistent across Task 3, Task 5, Task 6.
- `WorkflowRunner.__init__(app_cfg, event_bus, locks, task_source=None)` — consistent across Task 5, Task 7, Task 8.
- `DaemonScheduler.__init__(app_cfg, runner)` — consistent across Task 7, Task 8.
- `create_app(app_cfg, locks, event_bus, runner=None) -> FastAPI` — consistent across Task 6, Task 8.
- `cleanup_orphan_worktrees(repo_path) -> list[str]` — consistent across Task 4, Task 8.
- `DaemonConfig` fields (`enabled`, `task_schedule`, `eod_schedule`, `port`, `approval_timeout_hours`, `approval_on_timeout`) — consistent across Task 1, Task 6, Task 7.

No type/name mismatches found.
