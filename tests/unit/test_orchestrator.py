"""Unit tests for the orchestrator node."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from devflow.mcp.base import TaskSource
from devflow.nodes.orchestrator import orchestrator_node
from devflow.state import Task


class _FakeTaskSource(TaskSource):
    """In-memory task source for orchestrator tests."""

    name = "fake"

    def __init__(self, tasks: list[Task]) -> None:
        super().__init__({})
        self._tasks = {t.id: t for t in tasks}
        self.detail_calls: list[str] = []

    def fetch_tasks(self, status: str = "open", limit: int = 50) -> list[Task]:
        return [t for t in self._tasks.values() if t.status == status][:limit]

    def get_task_details(self, task_id: str) -> Task:
        self.detail_calls.append(task_id)
        if task_id not in self._tasks:
            raise ValueError(f"Task {task_id} not found")
        return self._tasks[task_id]

    def update_task_status(
        self, task_id: str, status: str, comment: str | None = None
    ) -> None:
        if task_id in self._tasks:
            self._tasks[task_id].status = status

    def close(self) -> None:
        pass


def _task(
    task_id: str,
    title: str,
    *,
    description: str = "desc",
    priority: str | None = "Нормальный",
    url: str | None = None,
) -> Task:
    return Task(
        id=task_id,
        title=title,
        description=description,
        status="open",
        metadata={"priority": priority, "redmine_url": url},
    )


def _state(**extra: Any) -> dict[str, Any]:
    return dict(extra)


# ---------------------------------------------------------------------------
# passthrough
# ---------------------------------------------------------------------------


def test_passthrough_when_task_already_in_state(tmp_path: Path) -> None:
    source = _FakeTaskSource([_task("MOCK-1", "t")])
    existing = _task("EXISTING", "already here")
    result = orchestrator_node(
        _state(task=existing),
        app_cfg=None,  # type: ignore[arg-type]
        todo_path=str(tmp_path / "TODO.md"),
        task_source=source,
    )
    assert result.get("task") is None  # passthrough returns no new task
    assert result["rework_count"] == 0
    # TODO file must NOT be created when a task is already present.
    assert not (tmp_path / "TODO.md").exists()


# ---------------------------------------------------------------------------
# generation when TODO missing
# ---------------------------------------------------------------------------


def test_generates_todo_when_missing(tmp_path: Path) -> None:
    path = tmp_path / "TODO.md"
    source = _FakeTaskSource(
        [
            _task("MOCK-1", "First", priority="Нормальный", url="https://t/1"),
            _task("MOCK-2", "Second", priority="Высокий", url="https://t/2"),
        ]
    )
    result = orchestrator_node(
        _state(),
        app_cfg=None,  # type: ignore[arg-type]
        todo_path=str(path),
        task_source=source,
    )
    assert path.exists()
    assert "error" not in result
    # r2 (Высокий=2) beats r3 (Нормальный=3): MOCK-2 selected first.
    task = result["task"]
    assert isinstance(task, Task)
    assert task.id == "MOCK-2"
    assert result["todo_item"].priority == 2


# ---------------------------------------------------------------------------
# selection from an existing file
# ---------------------------------------------------------------------------


def test_selects_lowest_r_from_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "TODO.md"
    path.write_text(
        "- [ ] #r3 [#A](https://t/a) — Low priority\n"
        "- [ ] #r0 [#B](https://t/b) — Immediate\n"
        "- [ ] #r2 — Human task\n",
        encoding="utf-8",
    )
    source = _FakeTaskSource(
        [
            _task("A", "Low priority", url="https://t/a"),
            _task("B", "Immediate", url="https://t/b"),
        ]
    )
    result = orchestrator_node(
        _state(),
        app_cfg=None,  # type: ignore[arg-type]
        todo_path=str(path),
        task_source=source,
    )
    assert result["task"].id == "B"
    # Tracker link was hydrated via get_task_details.
    assert source.detail_calls == ["B"]
    # The selected line is now in-progress.
    text = path.read_text(encoding="utf-8")
    assert "- [~] #r0 [#B](https://t/b) — Immediate" in text
    # Other lines untouched.
    assert "- [ ] #r3 [#A](https://t/a) — Low priority" in text


def test_human_entry_becomes_local_task(tmp_path: Path) -> None:
    path = tmp_path / "TODO.md"
    path.write_text("- [ ] #r1 — Just some text\n", encoding="utf-8")
    source = _FakeTaskSource([])
    result = orchestrator_node(
        _state(),
        app_cfg=None,  # type: ignore[arg-type]
        todo_path=str(path),
        task_source=source,
    )
    task = result["task"]
    assert task.id.startswith("local-")
    assert task.title == "Just some text"
    # No tracker detail lookup for a human entry.
    assert source.detail_calls == []


def test_bracket_ref_without_url_is_hydrated(tmp_path: Path) -> None:
    """[#id] without a URL still resolves to the tracker (per docs)."""
    path = tmp_path / "TODO.md"
    path.write_text("- [ ] #r1 [#42] — Short title\n", encoding="utf-8")
    source = _FakeTaskSource(
        [_task("42", "Full tracker title", description="detailed description")]
    )
    result = orchestrator_node(
        _state(),
        app_cfg=None,  # type: ignore[arg-type]
        todo_path=str(path),
        task_source=source,
    )
    task = result["task"]
    assert task.id == "42"
    # Hydrated from the tracker, not from the TODO title.
    assert source.detail_calls == ["42"]
    assert task.description == "detailed description"


# ---------------------------------------------------------------------------
# error / empty cases
# ---------------------------------------------------------------------------


def test_error_when_no_actionable_tasks(tmp_path: Path) -> None:
    path = tmp_path / "TODO.md"
    # All lines lack a #rX tag → nothing selectable.
    path.write_text("- [ ] No tag here\n- [x] #r1 — Done already\n", encoding="utf-8")
    source = _FakeTaskSource([])
    result = orchestrator_node(
        _state(),
        app_cfg=None,  # type: ignore[arg-type]
        todo_path=str(path),
        task_source=source,
    )
    assert "error" in result
    assert result.get("task") is None


def test_error_when_fetch_fails(tmp_path: Path) -> None:
    class _BrokenSource(_FakeTaskSource):
        def fetch_tasks(self, status: str = "open", limit: int = 50) -> list[Task]:
            raise RuntimeError("tracker down")

    # Missing file + broken source → generation fails → error.
    source = _BrokenSource([])
    result = orchestrator_node(
        _state(),
        app_cfg=None,  # type: ignore[arg-type]
        todo_path=str(tmp_path / "TODO.md"),
        task_source=source,
    )
    assert "error" in result
    assert "tracker down" in result["error"].message


def test_missing_todo_and_empty_source_yields_error(tmp_path: Path) -> None:
    """First run against a tracker with no open tasks: no crash, clear error."""
    path = tmp_path / "TODO.md"
    source = _FakeTaskSource([])  # no tasks at all
    result = orchestrator_node(
        _state(),
        app_cfg=None,  # type: ignore[arg-type]
        todo_path=str(path),
        task_source=source,
    )
    # A header-only TODO is generated; nothing is selectable → clear error.
    assert "error" in result
    assert result.get("task") is None
    assert path.exists()  # the (empty) file was still created
