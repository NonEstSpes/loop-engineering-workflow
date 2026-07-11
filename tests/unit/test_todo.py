"""Unit tests for :mod:`devflow.todo`."""

from __future__ import annotations

from pathlib import Path

import pytest

from devflow.state import Task
from devflow.todo import (
    CHECKBOX_DONE,
    CHECKBOX_IN_PROGRESS,
    CHECKBOX_OPEN,
    TodoItem,
    ensure_todo,
    generate_todo_from_source,
    mark_done,
    mark_in_progress,
    parse_todo,
    priority_from_task,
    render_todo,
    select_next_todo,
    write_todo,
)


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# parse_todo
# ---------------------------------------------------------------------------


class TestParseTodo:
    def test_parses_linked_task(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "TODO.md",
            "- [ ] #r2 [#251977](https://tracker/issues/251977) — Fix the bug\n",
        )
        items = parse_todo(path)
        assert len(items) == 1
        item = items[0]
        assert item.checkbox == CHECKBOX_OPEN
        assert item.priority == 2
        assert item.task_ref == "251977"
        assert item.url == "https://tracker/issues/251977"
        assert item.is_link is True
        assert item.title == "Fix the bug"
        assert item.line_no == 1

    def test_parses_bare_reference(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "TODO.md", "- [ ] #r1 #251977 — Title here\n")
        item = parse_todo(path)[0]
        assert item.task_ref == "251977"
        assert item.url is None
        # is_link requires both ref and url; bare refs are not auto-loaded.
        assert item.is_link is False

    def test_parses_human_entry(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "TODO.md", "- [ ] #r0 — Do something important\n")
        item = parse_todo(path)[0]
        assert item.priority == 0
        assert item.task_ref is None
        assert item.title == "Do something important"
        assert item.is_link is False

    def test_no_priority_tag_is_kept_but_not_actionable(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "TODO.md", "- [ ] Do something without tag\n")
        item = parse_todo(path)[0]
        assert item.checkbox == CHECKBOX_OPEN
        assert item.priority is None
        assert item.is_actionable is False

    def test_in_progress_and_done_checkboxes(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "TODO.md",
            "- [~] #r1 — In flight\n- [x] #r2 — Finished\n",
        )
        items = parse_todo(path)
        assert items[0].checkbox == CHECKBOX_IN_PROGRESS
        assert items[1].checkbox == CHECKBOX_DONE

    def test_non_task_lines_round_trip(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "TODO.md",
            "# TODO\n\nSome prose without checkbox.\n- [ ] #r1 — Real task\n",
        )
        items = parse_todo(path)
        assert items[0].checkbox is None  # heading
        assert items[1].checkbox is None  # blank line
        assert items[2].checkbox is None  # prose
        assert items[3].priority == 1  # the real task

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert parse_todo(tmp_path / "missing.md") == []

    def test_strips_existing_result_suffix(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "TODO.md",
            "- [x] #r1 [#42](https://t/42) — Title — ✅ done: All good\n",
        )
        item = parse_todo(path)[0]
        assert item.checkbox == CHECKBOX_DONE
        assert item.result == "All good"
        assert item.title == "Title"

    def test_r50_not_matched_as_r5(self, tmp_path: Path) -> None:
        # Word boundary: #r50 must not collapse to priority 5.
        path = _write(tmp_path / "TODO.md", "- [ ] #r50 — Weird tag\n")
        item = parse_todo(path)[0]
        assert item.priority is None


# ---------------------------------------------------------------------------
# select_next_todo
# ---------------------------------------------------------------------------


class TestSelectNextTodo:
    def test_lowest_r_wins(self) -> None:
        items = [
            TodoItem(raw_line="a", line_no=1, checkbox=CHECKBOX_OPEN, priority=3, task_ref=None, url=None, title="a", result=None),
            TodoItem(raw_line="b", line_no=2, checkbox=CHECKBOX_OPEN, priority=0, task_ref=None, url=None, title="b", result=None),
            TodoItem(raw_line="c", line_no=3, checkbox=CHECKBOX_OPEN, priority=2, task_ref=None, url=None, title="c", result=None),
        ]
        selected = select_next_todo(items)
        assert selected is not None
        assert selected.priority == 0

    def test_tie_broken_by_topmost_line(self) -> None:
        items = [
            TodoItem(raw_line="late", line_no=5, checkbox=CHECKBOX_OPEN, priority=1, task_ref=None, url=None, title="late", result=None),
            TodoItem(raw_line="early", line_no=2, checkbox=CHECKBOX_OPEN, priority=1, task_ref=None, url=None, title="early", result=None),
        ]
        selected = select_next_todo(items)
        assert selected is not None
        assert selected.line_no == 2

    def test_skips_in_progress_and_done(self) -> None:
        items = [
            TodoItem(raw_line="a", line_no=1, checkbox=CHECKBOX_IN_PROGRESS, priority=0, task_ref=None, url=None, title="a", result=None),
            TodoItem(raw_line="b", line_no=2, checkbox=CHECKBOX_DONE, priority=0, task_ref=None, url=None, title="b", result=None),
            TodoItem(raw_line="c", line_no=3, checkbox=CHECKBOX_OPEN, priority=3, task_ref=None, url=None, title="c", result=None),
        ]
        selected = select_next_todo(items)
        assert selected is not None
        assert selected.line_no == 3

    def test_skips_items_without_priority(self) -> None:
        items = [
            TodoItem(raw_line="a", line_no=1, checkbox=CHECKBOX_OPEN, priority=None, task_ref=None, url=None, title="a", result=None),
            TodoItem(raw_line="b", line_no=2, checkbox=CHECKBOX_OPEN, priority=2, task_ref=None, url=None, title="b", result=None),
        ]
        selected = select_next_todo(items)
        assert selected is not None
        assert selected.line_no == 2

    def test_returns_none_when_empty(self) -> None:
        assert select_next_todo([]) is None

    def test_returns_none_when_all_done(self) -> None:
        items = [
            TodoItem(raw_line="a", line_no=1, checkbox=CHECKBOX_DONE, priority=0, task_ref=None, url=None, title="a", result=None),
        ]
        assert select_next_todo(items) is None


# ---------------------------------------------------------------------------
# mark_in_progress / mark_done
# ---------------------------------------------------------------------------


class TestMarkInProgress:
    def test_replaces_single_line(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "TODO.md",
            "- [ ] #r1 — A\n- [ ] #r2 — B\n",
        )
        items = parse_todo(path)
        mark_in_progress(path, items[0])
        text = path.read_text(encoding="utf-8")
        assert text == "- [~] #r1 — A\n- [ ] #r2 — B\n"

    def test_preserves_other_lines(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "TODO.md",
            "# Heading\n\n- [ ] #r1 — A\n\nblurb\n",
        )
        items = parse_todo(path)
        task_item = next(i for i in items if i.checkbox == CHECKBOX_OPEN)
        mark_in_progress(path, task_item)
        text = path.read_text(encoding="utf-8")
        assert "# Heading" in text
        assert "blurb" in text
        assert "- [~] #r1 — A" in text


class TestMarkDone:
    def test_appends_done_suffix(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "TODO.md", "- [ ] #r1 [#42](https://t/42) — Title\n")
        item = parse_todo(path)[0]
        mark_done(path, item, "All good", kind="done")
        line = path.read_text(encoding="utf-8").strip()
        assert line.startswith("- [x]")
        assert "Title" in line
        assert "✅ done: All good" in line

    def test_problem_kind(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "TODO.md", "- [ ] #r1 — Title\n")
        item = parse_todo(path)[0]
        mark_done(path, item, "checker rejected", kind="problem")
        line = path.read_text(encoding="utf-8").strip()
        assert line.startswith("- [x]")
        assert "⚠️ problem: checker rejected" in line

    def test_replaces_existing_suffix(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "TODO.md",
            "- [x] #r1 — Title — ✅ done: Old\n",
        )
        item = parse_todo(path)[0]
        mark_done(path, item, "New result", kind="done")
        text = path.read_text(encoding="utf-8")
        assert "Old" not in text
        assert "New result" in text

    def test_relocates_after_reorder(self, tmp_path: Path) -> None:
        """If lines moved between orchestrator and reporter, find by ref."""
        path = _write(tmp_path / "TODO.md", "- [ ] #r1 [#42](https://t/42) — Title\n")
        item = parse_todo(path)[0]
        # Simulate the user reordering the file: another line now precedes ours.
        path.write_text("- [ ] #r0 — Other\n- [ ] #r1 [#42](https://t/42) — Title\n", encoding="utf-8")
        mark_done(path, item, "Done", kind="done")
        text = path.read_text(encoding="utf-8")
        # The ref-bearing line got updated, the other stays open.
        assert "- [ ] #r0 — Other" in text
        assert "- [x]" in text and "✅ done: Done" in text


# ---------------------------------------------------------------------------
# generate_todo_from_source / render / write / ensure
# ---------------------------------------------------------------------------


def _task(
    task_id: str,
    title: str,
    *,
    priority: str | None = "Нормальный",
    url: str | None = None,
) -> Task:
    return Task(
        id=task_id,
        title=title,
        description="desc",
        status="open",
        metadata={"priority": priority, "redmine_url": url},
    )


class TestPriorityFromTask:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("Немедленный", 0),
            ("Срочный", 1),
            ("Высокий", 2),
            ("Нормальный", 3),
            ("Низкий", 4),
            ("Unknown", 5),
            (None, 5),
        ],
    )
    def test_mapping(self, name: str | None, expected: int) -> None:
        assert priority_from_task(_task("1", "t", priority=name)) == expected


class TestGenerateTodoFromSource:
    def test_sorted_by_priority(self) -> None:
        tasks = [
            _task("10", "low", priority="Низкий", url="https://t/10"),
            _task("1", "urgent", priority="Немедленный", url="https://t/1"),
            _task("5", "normal", priority="Нормальный", url="https://t/5"),
        ]
        items = generate_todo_from_source(tasks)
        priorities = [it.priority for it in items]
        assert priorities == sorted(priorities)
        assert priorities[0] == 0  # immediate first

    def test_link_format(self) -> None:
        items = generate_todo_from_source([_task("42", "Title", url="https://t/42")])
        assert items[0].is_link
        assert items[0].task_ref == "42"
        assert items[0].url == "https://t/42"
        assert "[#42](https://t/42)" in items[0].raw_line

    def test_bare_ref_when_no_url(self) -> None:
        items = generate_todo_from_source([_task("42", "Title", url=None)])
        assert items[0].url is None
        assert "#42" in items[0].raw_line

    def test_unknown_priority_is_r5(self) -> None:
        items = generate_todo_from_source([_task("42", "Title", priority="???")])
        assert items[0].priority == 5


class TestRenderAndWrite:
    def test_render_without_header(self) -> None:
        items = generate_todo_from_source([_task("1", "A", url="https://t/1")])
        text = render_todo(items)
        assert "- [ ]" in text
        assert "#r" in text

    def test_render_with_header(self) -> None:
        items = generate_todo_from_source([_task("1", "A", url="https://t/1")])
        text = render_todo(items, header="# TODO\n\nGenerated.")
        assert text.startswith("# TODO")

    def test_write_then_parse_round_trip(self, tmp_path: Path) -> None:
        items = generate_todo_from_source([_task("1", "A", url="https://t/1")])
        path = tmp_path / "TODO.md"
        write_todo(path, items)
        parsed = parse_todo(path)
        assert len(parsed) == 1
        assert parsed[0].task_ref == "1"
        assert parsed[0].line_no == 1  # assigned on re-parse


class TestEnsureTodo:
    def test_creates_file_when_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "TODO.md"
        items = ensure_todo(
            path,
            lambda: generate_todo_from_source([_task("1", "A", url="https://t/1")]),
        )
        assert path.exists()
        assert len(items) == 1
        assert items[0].task_ref == "1"

    def test_uses_existing_file(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "TODO.md", "- [ ] #r1 — Existing\n")
        called = {"flag": False}

        def factory() -> list[TodoItem]:
            called["flag"] = True
            return []

        items = ensure_todo(path, factory)
        assert called["flag"] is False  # factory not invoked
        assert len(items) == 1
        assert items[0].title == "Existing"
