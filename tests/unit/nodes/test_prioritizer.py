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
    """If LLM call fails, the queue stays empty (orchestrator falls back)."""
    from devflow.nodes.prioritizer import prioritizer_node

    tasks_md = tmp_path / "TASKS.md"
    tasks_md.write_text("- [ ] #r0 #111 — Task A\n", encoding="utf-8")

    store = QueueStore(str(tmp_path / "queue.db"))
    cfg = MagicMock()
    cfg.workflow.todo_path = str(tasks_md)
    cfg.agents = {"prioritizer": MagicMock(system_prompt="x")}

    with patch("devflow.nodes.prioritizer.build_llm"), \
         patch("devflow.nodes.prioritizer.call_structured", side_effect=RuntimeError("LLM down")):
        result = prioritizer_node(
            state={},
            app_cfg=cfg,
            repo_path=str(tmp_path),
            queue_store=store,
        )

    # Queue should be empty (fallback).
    assert store.get_queue() == []
    assert "failed" in result["logs"][0].lower()


def test_prioritizer_appends_unevaluated_tasks(tmp_path: Path) -> None:
    """Tasks the LLM missed are appended to the end of the queue."""
    from devflow.nodes.prioritizer import prioritizer_node

    tasks_md = tmp_path / "TASKS.md"
    tasks_md.write_text(
        "- [ ] #r0 #111 — Task A\n"
        "- [ ] #r1 #222 — Task B\n"
        "- [ ] #r3 #333 — Task C\n",
        encoding="utf-8",
    )
    store = QueueStore(str(tmp_path / "queue.db"))
    cfg = MagicMock()
    cfg.workflow.todo_path = str(tasks_md)
    cfg.agents = {"prioritizer": MagicMock(system_prompt="x")}

    # LLM only returns 2 of 3 tasks.
    llm_result = PrioritizationResult(
        ordered_tasks=[
            PrioritizedTask(task_id="222", reason=""),
            PrioritizedTask(task_id="111", reason=""),
        ]
    )

    with patch("devflow.nodes.prioritizer.build_llm"), \
         patch("devflow.nodes.prioritizer.call_structured", return_value=llm_result):
        prioritizer_node(
            state={}, app_cfg=cfg, repo_path=str(tmp_path), queue_store=store,
        )

    queue = store.get_queue()
    # All 3 tasks present; task 333 (not evaluated) is last.
    assert len(queue) == 3
    assert queue[-1].task_id == "333"
    assert "not evaluated" in queue[-1].reason
