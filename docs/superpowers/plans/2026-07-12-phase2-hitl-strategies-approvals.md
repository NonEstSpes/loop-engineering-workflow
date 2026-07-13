# Phase 2: HITL Strategies + Approval Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement three human-in-the-loop strategies (`per_plan`, `full_detail`, `end_of_day`) that move the approval gate based on config, plus a local web-based approval bridge that replaces the (unavailable) Telegram channel, and ntfy/email push channels to notify the operator that an approval is pending.

**Architecture:** A new `publish_approval` node inserted between `aggregate_checker` and `reporter` provides the second gate (post-implementation review). The existing `plan_approval` node's auto-approval logic is extended to consult `hitl_strategy`. A new `ApprovalStore` (in-process dict of `thread_id → Future + payload`) bridges the LangGraph `interrupt()` to a FastAPI `/api/approvals` endpoint. The `WorkflowRunner` switches from `run_workflow` to `run_workflow_interactive` with an `ApprovalCallback` that blocks on the store. Two new `NotificationChannel` implementations (`ntfy`, `email`) push "approval pending" messages.

**Tech Stack:** Python ≥3.11, FastAPI (existing from Phase 1), httpx (existing `web` extra), LangGraph `interrupt()`/`Command(resume=...)` (existing), Pydantic (existing), pytest + pytest-asyncio (existing).

## Global Constraints

- Python ≥3.11 (pyproject.toml line 10)
- Line length 100, ruff rules: E, F, I, W, UP, B, C4, SIM (pyproject.toml lines 52-56)
- `asyncio_mode = "auto"` for pytest-asyncio (pyproject.toml line 67)
- `testpaths = ["tests"]`, `pythonpath = ["src"]` (pyproject.toml lines 68-69)
- All code and logs in English (only `_TODO_HEADER` in orchestrator.py is Russian)
- Config env-override pattern: `DEVFLOW_*` prefix
- `HitlStrategy` is a plain string-constant class (config.py:62-69): `PER_PLAN = "per_plan"`, `FULL_DETAIL = "full_detail"`, `END_OF_DAY = "end_of_day"`, `ALL = frozenset({...})`
- `WorkflowConfig.hitl_strategy: str = "per_plan"` (config.py:58)
- `DaemonConfig.approval_timeout_hours: int = 8`, `approval_on_timeout: str = "defer"` (config.py:79-80)
- Existing `ApprovalCallback = Callable[[dict[str, Any], WorkflowState], dict[str, Any]]` (graph.py:253); resume value shape: `{"approved": bool, "reason": str, "requested_changes": list[str]}`
- Existing `run_workflow_interactive(app_cfg, repo_path, task_id, task_source, initial_state, thread_id, approval_callback, *, max_resumptions=50)` (graph.py:256) handles the interrupt+resume loop
- Existing `NotificationChannel` ABC (notifications/base.py:9): `name: str`, `__init__(config: dict)`, `send(message, *, parse_mode=None) -> str`, `close()`, `healthcheck()`
- Existing `register_notification_channel(name, cls)` hook (notifications/factory.py:121)
- Existing `WorkflowRunner.run_task(task_id, repo_path, thread_id)` calls the NON-interactive `run_workflow` (Phase 1); Phase 2 switches to `run_workflow_interactive`
- EventBus has NO retention — messages published before subscription are dropped (daemon/events.py)

---

## File Structure

| File | Responsibility |
|---|---|
| `src/devflow/nodes/publish_approval.py` | NEW interrupt node: gate before reporter for `full_detail` strategy |
| `src/devflow/nodes/plan_approval.py` | Modify: extend auto-approve logic to consult `hitl_strategy` |
| `src/devflow/state.py` | Modify: add `publish_approved: bool \| None` field to `WorkflowState` |
| `src/devflow/graph.py` | Modify: register `publish_approval` node, update edges `_after_aggregate_checker` routing |
| `src/devflow/daemon/approval_store.py` | NEW: in-process store of pending approvals (`thread_id → Future + payload`) |
| `src/devflow/daemon/approval_bridge.py` | NEW: `ApprovalBridge` — builds `ApprovalCallback`, blocks on store, sends push, handles timeout |
| `src/devflow/daemon/runner.py` | Modify: switch `run_workflow` → `run_workflow_interactive` with bridge callback |
| `src/devflow/daemon/web.py` | Modify: add `GET /api/approvals`, `POST /api/approvals/{thread_id}` endpoints; wire `pending_approvals` count into health |
| `src/devflow/notifications/ntfy.py` | NEW: `NotificationChannel` impl for ntfy.sh push |
| `src/devflow/notifications/email_channel.py` | NEW: `NotificationChannel` impl for SMTP email |
| `src/devflow/notifications/factory.py` | Modify: register `ntfy` and `email` channels |
| `src/devflow/daemon/__main__.py` | Modify: wire ApprovalStore + ApprovalBridge into daemon startup |
| `config/workflow.yaml` | Modify: document ntfy/email channel config |
| `.env.example` | Modify: document `NTFY_*`, `SMTP_*` env vars |
| `tests/unit/daemon/test_approval_store.py` | NEW |
| `tests/unit/daemon/test_approval_bridge.py` | NEW |
| `tests/unit/daemon/test_web.py` | Modify: add approval endpoint tests |
| `tests/unit/daemon/test_runner.py` | Modify: update for interactive runner |
| `tests/unit/nodes/test_publish_approval.py` | NEW |
| `tests/unit/nodes/test_plan_approval_strategy.py` | NEW |
| `tests/unit/notifications/test_ntfy.py` | NEW |
| `tests/unit/notifications/test_email.py` | NEW |
| `tests/unit/test_graph.py` | Modify: test publish_approval node wiring |

---

## Task 1: Add `publish_approved` state field and `publish_approval` node

**Files:**
- Modify: `src/devflow/state.py:130-161` (WorkflowState TypedDict)
- Create: `src/devflow/nodes/publish_approval.py`
- Test: `tests/unit/nodes/test_publish_approval.py`, `tests/unit/nodes/__init__.py`

**Interfaces:**
- Consumes: `WorkflowState` (state.py:130), `Config` (config.py:100), `HitlStrategy` (config.py:62), `interrupt` from `langgraph.types`
- Produces: `publish_approval_node(state, *, app_cfg) -> dict` returning `{"publish_approved": bool, "logs": [...]}`. The node calls `interrupt()` with a payload containing `gate_type`, `task_id`, `plan_summary`, `diff`, `checker_reports`, `self_review_notes`, `branch`. Resume value contract: `{"approved": bool, "reason": str, "requested_changes": list[str]}`.

- [ ] **Step 1: Create test package init**

Create `tests/unit/nodes/__init__.py` (empty file).

- [ ] **Step 2: Write the failing test**

Create `tests/unit/nodes/test_publish_approval.py`:

```python
"""Unit tests for the publish_approval node."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from devflow.config import Config, HitlStrategy, WorkflowConfig
from devflow.schemas import Plan, PlanStep
from devflow.state import CheckerReport, CheckerVerdict, Task, WorkflowState


def _make_state(
    hitl_strategy: str = HitlStrategy.FULL_DETAIL,
    human_in_the_loop: bool = True,
) -> WorkflowState:
    """Build a minimal WorkflowState for publish_approval tests."""
    task = Task(id="T-1", title="Test", description="desc")
    plan = Plan(summary="Do thing", steps=[PlanStep(id="s1", description="step")])
    report = CheckerReport(
        agent_name="checker_a",
        verdict=CheckerVerdict.APPROVE,
        summary="ok",
    )
    return WorkflowState(
        task=task,
        plan=plan,
        diff="--- a\n+++ b\n",
        checker_reports=[report],
        self_review_notes="looks good",
        branch_name="devflow/T-1/abc12345",
    )


def _make_config(hitl_strategy: str, human_in_the_loop: bool) -> Config:
    """Build a minimal Config with the given strategy."""
    wf = WorkflowConfig(
        task_source="mock",
        human_in_the_loop=human_in_the_loop,
        hitl_strategy=hitl_strategy,
    )
    agents = {
        "publish_approval": type(
            "A",
            (),
            {"auto_approve": False, "system_prompt": "test"},
        )(),
    }
    return Config(workflow=wf, providers={}, agents=agents)  # type: ignore[arg-type]


def test_publish_approval_interrupts_in_full_detail() -> None:
    """In full_detail mode, the node calls interrupt() and waits for resume."""
    state = _make_state(hitl_strategy=HitlStrategy.FULL_DETAIL)
    cfg = _make_config(HitlStrategy.FULL_DETAIL, human_in_the_loop=True)

    resume_value = {"approved": True, "reason": "ok", "requested_changes": []}
    with patch("devflow.nodes.publish_approval.interrupt", return_value=resume_value) as mock_int:
        from devflow.nodes.publish_approval import publish_approval_node

        result = publish_approval_node(state, app_cfg=cfg)

    mock_int.assert_called_once()
    payload = mock_int.call_args[0][0]
    assert payload["gate_type"] == "publish_approval"
    assert payload["task_id"] == "T-1"
    assert payload["diff"] == "--- a\n+++ b\n"
    assert len(payload["checker_reports"]) == 1
    assert result["publish_approved"] is True


def test_publish_approval_auto_approves_in_per_plan() -> None:
    """In per_plan mode, publish is auto-approved (no interrupt)."""
    state = _make_state(hitl_strategy=HitlStrategy.PER_PLAN)
    cfg = _make_config(HitlStrategy.PER_PLAN, human_in_the_loop=True)

    with patch("devflow.nodes.publish_approval.interrupt") as mock_int:
        from devflow.nodes.publish_approval import publish_approval_node

        result = publish_approval_node(state, app_cfg=cfg)

    mock_int.assert_not_called()
    assert result["publish_approved"] is True


def test_publish_approval_auto_approves_in_end_of_day() -> None:
    """In end_of_day mode, publish is auto-approved (batch publish happens in Phase 4)."""
    state = _make_state(hitl_strategy=HitlStrategy.END_OF_DAY)
    cfg = _make_config(HitlStrategy.END_OF_DAY, human_in_the_loop=True)

    with patch("devflow.nodes.publish_approval.interrupt") as mock_int:
        from devflow.nodes.publish_approval import publish_approval_node

        result = publish_approval_node(state, app_cfg=cfg)

    mock_int.assert_not_called()
    assert result["publish_approved"] is True


def test_publish_approval_auto_approves_when_human_loop_off() -> None:
    """When human_in_the_loop is off, no interrupt regardless of strategy."""
    state = _make_state(hitl_strategy=HitlStrategy.FULL_DETAIL)
    cfg = _make_config(HitlStrategy.FULL_DETAIL, human_in_the_loop=False)

    with patch("devflow.nodes.publish_approval.interrupt") as mock_int:
        from devflow.nodes.publish_approval import publish_approval_node

        result = publish_approval_node(state, app_cfg=cfg)

    mock_int.assert_not_called()
    assert result["publish_approved"] is True


def test_publish_approval_rejection_propagates() -> None:
    """A rejection via resume value sets publish_approved=False."""
    state = _make_state(hitl_strategy=HitlStrategy.FULL_DETAIL)
    cfg = _make_config(HitlStrategy.FULL_DETAIL, human_in_the_loop=True)

    resume_value = {"approved": False, "reason": "bad diff", "requested_changes": ["fix X"]}
    with patch("devflow.nodes.publish_approval.interrupt", return_value=resume_value):
        from devflow.nodes.publish_approval import publish_approval_node

        result = publish_approval_node(state, app_cfg=cfg)

    assert result["publish_approved"] is False
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/unit/nodes/test_publish_approval.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devflow.nodes.publish_approval'`

- [ ] **Step 4: Add `publish_approved` to WorkflowState**

In `src/devflow/state.py`, add the field to `WorkflowState` TypedDict (after `plan_approved`, around line 142):

```python
    publish_approved: bool | None
```

- [ ] **Step 5: Write the publish_approval_node implementation**

Create `src/devflow/nodes/publish_approval.py`:

```python
"""Publish approval node: the post-implementation human-in-the-loop gate.

This is the SECOND gate (after plan_approval). It sits between
aggregate_checker and reporter. In ``full_detail`` strategy it pauses the
workflow via ``interrupt()`` so the human can review the diff, checker
reports, and self-review before publication. In ``per_plan`` and
``end_of_day`` it auto-approves (no pause).
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.types import interrupt

from devflow.config import Config, HitlStrategy
from devflow.state import WorkflowError, WorkflowState

logger = logging.getLogger(__name__)


def publish_approval_node(
    state: WorkflowState,
    *,
    app_cfg: Config,
) -> dict[str, Any]:
    """Gate before the reporter: approve or reject the finished work.

    Strategy behaviour:
    - ``per_plan``: auto-approve (the plan was already approved).
    - ``full_detail``: interrupt and ask the human to review diff + checkers.
    - ``end_of_day``: auto-approve (batch publish is handled in Phase 4).

    When ``human_in_the_loop`` is off, auto-approve regardless of strategy.
    """
    task = state.get("task")
    if task is None:
        return {
            "error": WorkflowError(
                node="publish_approval",
                message="Missing task in state",
            ),
            "logs": ["publish_approval: error - missing task"],
        }

    strategy = app_cfg.workflow.hitl_strategy
    hil = app_cfg.workflow.human_in_the_loop

    # Auto-approve unless we are in full_detail mode with HITL on.
    should_interrupt = hil and strategy == HitlStrategy.FULL_DETAIL

    if not should_interrupt:
        logger.info(
            "publish_approval: auto-approve (strategy=%s, hitl=%s)", strategy, hil
        )
        return {"publish_approved": True, "logs": ["publish_approval: auto-approved"]}

    # Build the interrupt payload for the human to review.
    plan = state.get("plan")
    diff = state.get("diff") or ""
    reports = state.get("checker_reports", [])
    self_review_notes = state.get("self_review_notes")
    branch = state.get("branch_name")

    payload: dict[str, Any] = {
        "gate_type": "publish_approval",
        "task_id": task.id,
        "task_title": task.title,
        "plan_summary": plan.summary if plan else "",
        "plan_steps": (
            [{"id": s.id, "description": s.description} for s in plan.steps]
            if plan
            else []
        ),
        "diff": diff,
        "checker_reports": [
            {
                "agent_name": r.agent_name,
                "verdict": r.verdict.value,
                "summary": r.summary,
                "findings": r.findings,
            }
            for r in reports
        ],
        "self_review_notes": self_review_notes or "",
        "branch": branch,
    }

    logger.info("publish_approval: interrupting for human review (task %s)", task.id)

    resume_value = interrupt(payload)
    approved = bool(resume_value.get("approved"))
    reason = str(resume_value.get("reason", ""))
    requested_changes = list(resume_value.get("requested_changes", []))

    logger.info(
        "publish_approval: decision=%s, reason=%s, changes=%d",
        approved,
        reason,
        len(requested_changes),
    )

    return {
        "publish_approved": approved,
        "logs": [
            f"publish_approval: {'approved' if approved else 'rejected'}"
            + (f" — {reason}" if reason else ""),
        ],
    }
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/unit/nodes/test_publish_approval.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/devflow/nodes/publish_approval.py src/devflow/state.py tests/unit/nodes/__init__.py tests/unit/nodes/test_publish_approval.py
git commit -m "feat(nodes): add publish_approval node (full_detail gate)

Second HITL gate between aggregate_checker and reporter. Interrupts in
full_detail mode with diff+checker payload; auto-approves in per_plan
and end_of_day. Adds publish_approved field to WorkflowState."
```

---

## Task 2: Wire publish_approval into the graph

**Files:**
- Modify: `src/devflow/graph.py:36-146` (build_graph), `src/devflow/graph.py:194-215` (_after_aggregate_checker)
- Test: `tests/unit/test_graph.py`

**Interfaces:**
- Consumes: `publish_approval_node` from Task 1, existing `_after_aggregate_checker` router (graph.py:194)
- Produces: graph now routes `aggregate_checker → publish_approval → reporter` (when APPROVE) or `aggregate_checker → maker` (rework). The `publish_approval` node must always run before reporter on the happy path.

- [ ] **Step 1: Read current _after_aggregate_checker and edge wiring**

Read `src/devflow/graph.py` lines 129-133 (the aggregate_checker edge) and 194-215 (_after_aggregate_checker) to understand current routing: on APPROVE → reporter; on REJECT/CONDITIONAL → maker (rework) or reporter (max rework).

- [ ] **Step 2: Write the failing test**

Add to `tests/unit/test_graph.py` (or create if it doesn't exist a section for publish_approval):

```python
"""Test that publish_approval node is wired into the graph."""

from __future__ import annotations

from devflow.config import Config, HitlStrategy
from devflow.graph import build_graph


def test_graph_has_publish_approval_node(mock_config: Config) -> None:
    """The compiled graph should include a publish_approval node."""
    mock_config.workflow.hitl_strategy = HitlStrategy.FULL_DETAIL
    graph = build_graph(app_cfg=mock_config)
    # The compiled graph's nodes are accessible via .nodes
    node_names = set(graph.nodes.keys())
    assert "publish_approval" in node_names


def test_graph_routes_aggregate_to_publish_on_approve(mock_config: Config) -> None:
    """On APPROVE verdict, aggregate_checker routes to publish_approval (not reporter)."""
    mock_config.workflow.hitl_strategy = HitlStrategy.FULL_DETAIL
    graph = build_graph(app_cfg=mock_config)
    node_names = set(graph.nodes.keys())
    # Both publish_approval and reporter must exist
    assert "publish_approval" in node_names
    assert "reporter" in node_names
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_graph.py::test_graph_has_publish_approval_node tests/unit/test_graph.py::test_graph_routes_aggregate_to_publish_on_approve -v`
Expected: FAIL — `"publish_approval" not in node_names`.

- [ ] **Step 4: Modify build_graph to register publish_approval node**

In `src/devflow/graph.py`, add import after line 24 (`from devflow.nodes.plan_approval import plan_approval_node`):

```python
from devflow.nodes.publish_approval import publish_approval_node
```

Add the node registration after the `aggregate_checker` node (after line 96, before `reporter`):

```python
    graph.add_node(
        "publish_approval",
        partial(publish_approval_node, app_cfg=app_cfg),
    )
```

- [ ] **Step 5: Modify the aggregate_checker edge to route through publish_approval**

Change the conditional edge for `aggregate_checker` (lines 129-133). The `_after_aggregate_checker` router currently returns `"maker"` (rework) or `"reporter"` (done). Change the `"reporter"` mapping to `"publish_approval"` so the happy path goes through the gate:

Replace lines 129-133:
```python
    graph.add_conditional_edges(
        "aggregate_checker",
        partial(_after_aggregate_checker, app_cfg=app_cfg),
        {"maker": "maker", "reporter": "reporter"},
    )
```

With:
```python
    graph.add_conditional_edges(
        "aggregate_checker",
        partial(_after_aggregate_checker, app_cfg=app_cfg),
        {"maker": "maker", "publish_approval": "publish_approval"},
    )
```

- [ ] **Step 6: Modify _after_aggregate_checker to return "publish_approval" instead of "reporter"**

In `_after_aggregate_checker` (graph.py:194-215), replace every `return "reporter"` that represents the "done/approved" path with `return "publish_approval"`. Specifically:

- Line 198 (error path): keep as `return "reporter"` — errors skip the gate.
- Line 205 (APPROVE): change `return "reporter"` to `return "publish_approval"`.
- Line 210 (max rework): keep as `return "reporter"` — escalation skips the gate.
- Line 214 (ESCALATE): keep as `return "reporter"`.

The function should read (after edits):

```python
def _after_aggregate_checker(state: WorkflowState, *, app_cfg: Config) -> str:
    """Route after checker aggregation: maker for rework, publish_approval or reporter."""
    if state.get("error"):
        logger.info("Routing aggregate_checker -> reporter due to error")
        return "reporter"

    verdict = state.get("final_verdict")
    rework_count = state.get("rework_count", 0)
    max_rework = app_cfg.workflow.max_rework_iterations

    if verdict == FinalVerdict.APPROVE:
        return "publish_approval"

    if verdict in {FinalVerdict.REJECT, FinalVerdict.CONDITIONAL}:
        if rework_count < max_rework:
            return "maker"
        logger.info("Routing aggregate_checker -> reporter (escalate: max rework)")
        return "reporter"

    # ESCALATE or unknown verdict
    logger.info("Routing aggregate_checker -> reporter (escalate)")
    return "reporter"
```

- [ ] **Step 7: Add edge from publish_approval to reporter**

After the publish_approval node registration and before `graph.add_edge("reporter", END)` (line 144), add:

```python
    # publish_approval -> reporter (the gate always proceeds to reporter after the decision)
    graph.add_edge("publish_approval", "reporter")
```

- [ ] **Step 8: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_graph.py::test_graph_has_publish_approval_node tests/unit/test_graph.py::test_graph_routes_aggregate_to_publish_on_approve -v`
Expected: PASS.

- [ ] **Step 9: Run full test suite to check no regressions**

Run: `python -m pytest tests/ -v --ignore=tests/integration`
Expected: All tests PASS. Note: the `test_runner.py` tests that run the full graph may now hit the `publish_approval` node — in `mock_config` the default strategy is `per_plan` so it auto-approves, no interrupt. Verify no test hangs.

- [ ] **Step 10: Commit**

```bash
git add src/devflow/graph.py tests/unit/test_graph.py
git commit -m "feat(graph): wire publish_approval node between checker and reporter

aggregate_checker now routes APPROVE -> publish_approval -> reporter.
Error/escalate paths still route directly to reporter. per_plan config
auto-approves at the gate so existing tests don't hang."
```

---

## Task 3: Extend plan_approval to consult hitl_strategy

**Files:**
- Modify: `src/devflow/nodes/plan_approval.py:40-45` (auto-approval condition)
- Test: `tests/unit/nodes/test_plan_approval_strategy.py`

**Interfaces:**
- Consumes: `HitlStrategy` (config.py:62), existing `plan_approval_node` (plan_approval.py:16)
- Produces: `plan_approval_node` now auto-approves (skips interrupt) when `hitl_strategy in {full_detail, end_of_day}` — the plan review happens later (full_detail reviews at publish gate) or not at all (end_of_day).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/nodes/test_plan_approval_strategy.py`:

```python
"""Test that plan_approval respects hitl_strategy."""

from __future__ import annotations

from unittest.mock import patch

from devflow.config import Config, HitlStrategy, WorkflowConfig
from devflow.schemas import Plan, PlanStep
from devflow.state import Task, WorkflowState


def _make_config(strategy: str) -> Config:
    wf = WorkflowConfig(
        task_source="mock",
        human_in_the_loop=True,
        hitl_strategy=strategy,
    )
    agents = {
        "plan_approval": type(
            "A",
            (),
            {"auto_approve": False, "system_prompt": "test"},
        )(),
    }
    return Config(workflow=wf, providers={}, agents=agents)  # type: ignore[arg-type]


def _make_state() -> WorkflowState:
    task = Task(id="T-1", title="Test", description="desc")
    plan = Plan(summary="Do thing", steps=[PlanStep(id="s1", description="step")])
    return WorkflowState(task=task, plan=plan)


def test_plan_approval_interrupts_in_per_plan() -> None:
    """In per_plan mode, the plan is interrupted for human approval."""
    state = _make_state()
    cfg = _make_config(HitlStrategy.PER_PLAN)

    resume = {"approved": True, "reason": "ok", "requested_changes": []}
    with patch("devflow.nodes.plan_approval.interrupt", return_value=resume) as mock_int:
        from devflow.nodes.plan_approval import plan_approval_node

        result = plan_approval_node(state, app_cfg=cfg)

    mock_int.assert_called_once()
    assert result["plan_approved"] is True


def test_plan_approval_auto_approves_in_full_detail() -> None:
    """In full_detail mode, the plan is auto-approved (review happens at publish gate)."""
    state = _make_state()
    cfg = _make_config(HitlStrategy.FULL_DETAIL)

    with patch("devflow.nodes.plan_approval.interrupt") as mock_int:
        from devflow.nodes.plan_approval import plan_approval_node

        result = plan_approval_node(state, app_cfg=cfg)

    mock_int.assert_not_called()
    assert result["plan_approved"] is True


def test_plan_approval_auto_approves_in_end_of_day() -> None:
    """In end_of_day mode, the plan is auto-approved."""
    state = _make_state()
    cfg = _make_config(HitlStrategy.END_OF_DAY)

    with patch("devflow.nodes.plan_approval.interrupt") as mock_int:
        from devflow.nodes.plan_approval import plan_approval_node

        result = plan_approval_node(state, app_cfg=cfg)

    mock_int.assert_not_called()
    assert result["plan_approved"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/nodes/test_plan_approval_strategy.py -v`
Expected: FAIL — `test_plan_approval_auto_approves_in_full_detail` and `test_plan_approval_auto_approves_in_end_of_day` will fail because `interrupt` is called (the mock's default return value is a MagicMock, not a dict, so `.get()` fails).

- [ ] **Step 3: Modify plan_approval_node auto-approve condition**

In `src/devflow/nodes/plan_approval.py`, add import at the top (after existing imports, around line 6):

```python
from devflow.config import HitlStrategy
```

Change the auto-approval condition (currently line 40):

```python
    if not app_cfg.workflow.human_in_the_loop or agent_cfg.auto_approve:
```

To:

```python
    strategy = app_cfg.workflow.hitl_strategy
    if (
        not app_cfg.workflow.human_in_the_loop
        or agent_cfg.auto_approve
        or strategy in {HitlStrategy.FULL_DETAIL, HitlStrategy.END_OF_DAY}
    ):
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/nodes/test_plan_approval_strategy.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Run existing plan_approval tests to check no regressions**

Run: `python -m pytest tests/unit/test_runner.py -v`
Expected: All existing tests PASS (mock_config has `hitl_strategy=per_plan` by default, so the interrupt path still fires when `human_in_the_loop=True`).

- [ ] **Step 6: Commit**

```bash
git add src/devflow/nodes/plan_approval.py tests/unit/nodes/test_plan_approval_strategy.py
git commit -m "feat(nodes): plan_approval auto-approves in full_detail/end_of_day

The plan review gate is skipped when the strategy defers the human
review to the publish gate (full_detail) or removes per-task review
entirely (end_of_day). per_plan keeps the existing interrupt behavior."
```

---

## Task 4: ApprovalStore — in-process pending approvals

**Files:**
- Create: `src/devflow/daemon/approval_store.py`
- Test: `tests/unit/daemon/test_approval_store.py`

**Interfaces:**
- Consumes: nothing (standalone)
- Produces: `ApprovalStore` class with `register(thread_id, payload) -> threading.Event`, `resolve(thread_id, decision) -> bool`, `wait(thread_id, timeout) -> dict | None`, `get_pending() -> list[dict]`, `remove(thread_id)`. Decision shape: `{"approved": bool, "reason": str, "requested_changes": list[str]}`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/daemon/test_approval_store.py`:

```python
"""Unit tests for the in-process ApprovalStore."""

from __future__ import annotations

import threading
import time

from devflow.daemon.approval_store import ApprovalStore


def test_register_and_get_pending() -> None:
    """A registered approval shows up in get_pending."""
    store = ApprovalStore()
    payload = {"gate_type": "plan_approval", "task_id": "T-1"}
    store.register("thread-1", payload)

    pending = store.get_pending()
    assert len(pending) == 1
    assert pending[0]["thread_id"] == "thread-1"
    assert pending[0]["payload"]["task_id"] == "T-1"


def test_resolve_unblocks_wait() -> None:
    """Resolving an approval unblocks a waiting thread."""
    store = ApprovalStore()
    store.register("thread-1", {"gate_type": "plan_approval", "task_id": "T-1"})

    result_holder: dict = {}

    def waiter() -> None:
        result_holder["decision"] = store.wait("thread-1", timeout=2.0)

    t = threading.Thread(target=waiter)
    t.start()

    # Give the waiter a moment to start blocking.
    time.sleep(0.05)

    decision = {"approved": True, "reason": "ok", "requested_changes": []}
    resolved = store.resolve("thread-1", decision)

    t.join(timeout=2.0)

    assert resolved is True
    assert result_holder["decision"] == decision


def test_wait_returns_none_on_timeout() -> None:
    """wait() returns None if no decision arrives within the timeout."""
    store = ApprovalStore()
    store.register("thread-1", {"gate_type": "plan_approval", "task_id": "T-1"})

    result = store.wait("thread-1", timeout=0.1)
    assert result is None
    # The approval is still pending after a timeout
    assert len(store.get_pending()) == 1


def test_resolve_unknown_thread_returns_false() -> None:
    """Resolving a thread that was never registered returns False."""
    store = ApprovalStore()
    decision = {"approved": True, "reason": "", "requested_changes": []}
    assert store.resolve("unknown", decision) is False


def test_remove_clears_entry() -> None:
    """remove() deletes a pending approval."""
    store = ApprovalStore()
    store.register("thread-1", {"task_id": "T-1"})
    store.remove("thread-1")
    assert store.get_pending() == []


def test_resolve_clears_entry() -> None:
    """After resolve, the entry is no longer pending."""
    store = ApprovalStore()
    store.register("thread-1", {"task_id": "T-1"})
    store.resolve("thread-1", {"approved": True, "reason": "", "requested_changes": []})
    assert store.get_pending() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_approval_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devflow.daemon.approval_store'`

- [ ] **Step 3: Write minimal implementation**

Create `src/devflow/daemon/approval_store.py`:

```python
"""In-process store of pending human approvals.

When the LangGraph workflow hits an ``interrupt()`` (plan_approval or
publish_approval), the ApprovalBridge registers the interrupt payload here
and blocks on ``wait()``. The FastAPI ``POST /api/approvals/{thread_id}``
endpoint calls ``resolve()`` to deliver the human's decision, unblocking the
workflow.

Thread-safe: ``register``/``resolve``/``remove`` use a ``threading.Lock``;
``wait`` blocks on a per-thread ``threading.Event``.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


class _PendingApproval:
    """A single pending approval: payload + event + decision."""

    def __init__(self, thread_id: str, payload: dict[str, Any]) -> None:
        self.thread_id = thread_id
        self.payload = payload
        self.event = threading.Event()
        self.decision: dict[str, Any] | None = None


class ApprovalStore:
    """Thread-safe registry of pending human approvals."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, _PendingApproval] = {}

    def register(self, thread_id: str, payload: dict[str, Any]) -> None:
        """Register a new pending approval. Overwrites if thread_id exists."""
        with self._lock:
            self._pending[thread_id] = _PendingApproval(thread_id, payload)
        logger.info("Registered pending approval for thread %s", thread_id)

    def resolve(self, thread_id: str, decision: dict[str, Any]) -> bool:
        """Deliver a human decision to a pending approval.

        Returns True if the thread_id was found and resolved, False otherwise.
        """
        with self._lock:
            entry = self._pending.get(thread_id)
            if entry is None:
                logger.warning("resolve: unknown thread_id %s", thread_id)
                return False
            entry.decision = decision
            entry.event.set()
            # Remove from pending immediately so get_pending() reflects the change.
            del self._pending[thread_id]
        logger.info("Resolved approval for thread %s: approved=%s", thread_id, decision.get("approved"))
        return True

    def wait(self, thread_id: str, timeout: float) -> dict[str, Any] | None:
        """Block until a decision is available or timeout expires.

        Returns the decision dict, or None on timeout.
        """
        with self._lock:
            entry = self._pending.get(thread_id)
        if entry is None:
            logger.warning("wait: unknown thread_id %s", thread_id)
            return None
        if entry.event.wait(timeout=timeout):
            return entry.decision
        logger.warning("wait: timeout for thread %s after %ss", thread_id, timeout)
        return None

    def get_pending(self) -> list[dict[str, Any]]:
        """Return a list of pending approvals with their payloads.

        Each entry: ``{"thread_id": str, "payload": dict}``.
        """
        with self._lock:
            return [
                {"thread_id": e.thread_id, "payload": e.payload}
                for e in self._pending.values()
            ]

    def remove(self, thread_id: str) -> None:
        """Remove a pending approval (e.g. after timeout or cancellation)."""
        with self._lock:
            self._pending.pop(thread_id, None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_approval_store.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devflow/daemon/approval_store.py tests/unit/daemon/test_approval_store.py
git commit -m "feat(daemon): add ApprovalStore for pending human approvals

Thread-safe registry: register/wait/resolve/get_pending/remove. wait()
blocks on a threading.Event; resolve() delivers the decision and
unblocks the waiting workflow thread."
```

---

## Task 5: ntfy notification channel

**Files:**
- Create: `src/devflow/notifications/ntfy.py`
- Modify: `src/devflow/notifications/factory.py:28` (register ntfy)
- Test: `tests/unit/notifications/test_ntfy.py`

**Interfaces:**
- Consumes: `NotificationChannel` ABC (notifications/base.py:9), `httpx` (existing `web` extra)
- Produces: `NtfyChannel(NotificationChannel)` with `name = "ntfy"`. Config keys: `server` (default `https://ntfy.sh`), `topic` (required), `token` (optional, for self-hosted auth). `send(message, *, parse_mode=None) -> str` returns the ntfy message URL.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/notifications/test_ntfy.py`:

```python
"""Unit tests for the ntfy notification channel."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from devflow.notifications.ntfy import NtfyChannel


def test_ntfy_channel_name() -> None:
    """The channel name is 'ntfy'."""
    channel = NtfyChannel({"topic": "test-topic"})
    assert channel.name == "ntfy"


def test_ntfy_send_publishes_message() -> None:
    """send() POSTs the message to the ntfy server."""
    channel = NtfyChannel({"topic": "my-topic", "server": "https://ntfy.sh"})

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()

    with patch("devflow.notifications.ntfy.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value.__enter__.return_value = mock_client

        result = channel.send("Hello ntfy", parse_mode=None)

    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert call_args[0][0] == "https://ntfy.sh/my-topic"
    assert "Hello ntfy" in call_args[1]["content"]
    assert result is not None
    assert "my-topic" in result


def test_ntfy_send_with_custom_server() -> None:
    """send() uses a custom server URL when configured."""
    channel = NtfyChannel({"topic": "dev", "server": "https://ntfy.internal.corp"})

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()

    with patch("devflow.notifications.ntfy.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value.__enter__.return_value = mock_client

        channel.send("test")

    call_args = mock_client.post.call_args
    assert call_args[0][0] == "https://ntfy.internal.corp/dev"


def test_ntfy_healthcheck_without_topic_returns_false() -> None:
    """healthcheck() returns False when no topic is configured."""
    channel = NtfyChannel({})
    assert channel.healthcheck() is False


def test_ntfy_healthcheck_with_topic_returns_true() -> None:
    """healthcheck() returns True when a topic is configured."""
    channel = NtfyChannel({"topic": "my-topic"})
    assert channel.healthcheck() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/notifications/test_ntfy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devflow.notifications.ntfy'`

- [ ] **Step 3: Write minimal implementation**

Create `src/devflow/notifications/ntfy.py`:

```python
"""ntfy.sh notification channel.

Sends push notifications via the ntfy protocol (HTTP POST to a topic).
Works with the public ntfy.sh server or a self-hosted instance. Supports
optional authentication via a bearer token (for self-hosted with auth).

Environment variables (read if not in config dict):
    NTFY_SERVER  — base URL (default: https://ntfy.sh)
    NTFY_TOPIC   — topic to publish to
    NTFY_TOKEN   — optional bearer token for auth
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from devflow.notifications.base import NotificationChannel

logger = logging.getLogger(__name__)


class NtfyChannel(NotificationChannel):
    """Push notifications via ntfy.sh or a self-hosted ntfy server."""

    name = "ntfy"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._server = config.get("server") or os.getenv("NTFY_SERVER", "https://ntfy.sh")
        self._topic = config.get("topic") or os.getenv("NTFY_TOPIC", "")
        self._token = config.get("token") or os.getenv("NTFY_TOKEN", "")
        self._client: httpx.Client | None = None

    def send(self, message: str, *, parse_mode: str | None = None) -> str:
        """POST ``message`` to the ntfy topic. Returns the topic URL."""
        if not self._topic:
            raise ValueError("ntfy channel requires a 'topic' (set NTFY_TOPIC or config)")

        url = f"{self._server}/{self._topic}"
        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        # Use a fresh client per send (short-lived). The daemon sends infrequently.
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, content=message, headers=headers)
            resp.raise_for_status()

        logger.info("ntfy: published to topic '%s'", self._topic)
        return url

    def healthcheck(self) -> bool:
        """Return True when a topic is configured."""
        return bool(self._topic)
```

- [ ] **Step 4: Register ntfy in the factory**

In `src/devflow/notifications/factory.py`, add `"ntfy"` to the `_OPTIONAL_CHANNELS` set (line 38):

```python
_OPTIONAL_CHANNELS = {"telegram", "ntfy"}
```

Add a lazy import branch inside `build_notification_channels`, after the telegram block (after line 91). Add a new `elif name == "ntfy"` before the generic else (around line 92):

```python
        elif name == "ntfy":
            try:
                import httpx  # noqa: F401
            except ImportError:
                logger.warning(
                    "Notification channel 'ntfy' requires the optional 'httpx' "
                    "package; install it with `pip install -e '.[web]'`. "
                    "Skipping 'ntfy'.",
                )
                continue
            from devflow.notifications.ntfy import NtfyChannel

            cls = NtfyChannel
```

And in the config-dict construction section (around line 105-114), add an `elif name == "ntfy"` branch:

```python
        elif name == "ntfy":
            config = {
                "server": os.getenv("NTFY_SERVER", extra.get("ntfy", {}).get("server", "")),
                "topic": os.getenv("NTFY_TOPIC", extra.get("ntfy", {}).get("topic", "")),
                "token": os.getenv("NTFY_TOKEN", extra.get("ntfy", {}).get("token", "")),
            }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/notifications/test_ntfy.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 6: Run factory tests to check no regressions**

Run: `python -m pytest tests/unit/notifications/test_factory.py -v`
Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/devflow/notifications/ntfy.py src/devflow/notifications/factory.py tests/unit/notifications/test_ntfy.py
git commit -m "feat(notifications): add ntfy.sh push channel

NtfyChannel POSTs messages to a topic on ntfy.sh or a self-hosted
server. Reads NTFY_SERVER/NTFY_TOPIC/NTFY_TOKEN from env. Registered
in factory as an optional channel (requires httpx)."
```

---

## Task 6: Email notification channel

**Files:**
- Create: `src/devflow/notifications/email_channel.py`
- Modify: `src/devflow/notifications/factory.py` (register email)
- Test: `tests/unit/notifications/test_email.py`

**Interfaces:**
- Consumes: `NotificationChannel` ABC (notifications/base.py:9), `smtplib` + `email.message` (stdlib)
- Produces: `EmailChannel(NotificationChannel)` with `name = "email"`. Config keys: `smtp_host` (required), `smtp_port` (default 587), `smtp_user`, `smtp_password`, `from_addr` (required), `to_addr` (required). `send(message, *, parse_mode=None) -> str` returns a `mailto:` style identifier.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/notifications/test_email.py`:

```python
"""Unit tests for the email notification channel."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from devflow.notifications.email_channel import EmailChannel


def test_email_channel_name() -> None:
    """The channel name is 'email'."""
    channel = EmailChannel({
        "smtp_host": "smtp.example.com",
        "from_addr": "bot@corp.com",
        "to_addr": "dev@corp.com",
    })
    assert channel.name == "email"


def test_email_send_uses_smtp() -> None:
    """send() connects to SMTP and sends the message."""
    channel = EmailChannel({
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_user": "bot",
        "smtp_password": "pass",
        "from_addr": "bot@corp.com",
        "to_addr": "dev@corp.com",
    })

    with patch("devflow.notifications.email_channel.smtplib.SMTP") as mock_smtp:
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_server

        result = channel.send("Task T-1 approved", parse_mode=None)

    mock_smtp.assert_called_once_with("smtp.example.com", 587, timeout=30)
    mock_server.starttls.assert_called_once()
    mock_server.login.assert_called_once_with("bot", "pass")
    mock_server.send_message.assert_called_once()
    assert "dev@corp.com" in result


def test_email_healthcheck_without_host_returns_false() -> None:
    """healthcheck() returns False when smtp_host is missing."""
    channel = EmailChannel({"from_addr": "a@b.com", "to_addr": "c@d.com"})
    assert channel.healthcheck() is False


def test_email_healthcheck_with_config_returns_true() -> None:
    """healthcheck() returns True when smtp_host, from, and to are set."""
    channel = EmailChannel({
        "smtp_host": "smtp.example.com",
        "from_addr": "bot@corp.com",
        "to_addr": "dev@corp.com",
    })
    assert channel.healthcheck() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/notifications/test_email.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devflow.notifications.email_channel'`

- [ ] **Step 3: Write minimal implementation**

Create `src/devflow/notifications/email_channel.py`:

```python
"""Email notification channel via SMTP.

Sends notifications as plain-text emails. Uses STARTTLS by default.
Environment variables (read if not in config dict):
    SMTP_HOST     — SMTP server hostname
    SMTP_PORT     — SMTP port (default: 587)
    SMTP_USER     — SMTP username
    SMTP_PASSWORD — SMTP password
    EMAIL_FROM    — sender address
    EMAIL_TO      — recipient address
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Any

from devflow.notifications.base import NotificationChannel

logger = logging.getLogger(__name__)


class EmailChannel(NotificationChannel):
    """Send notifications via SMTP email."""

    name = "email"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._host = config.get("smtp_host") or os.getenv("SMTP_HOST", "")
        self._port = int(config.get("smtp_port", 0) or os.getenv("SMTP_PORT", "587"))
        self._user = config.get("smtp_user") or os.getenv("SMTP_USER", "")
        self._password = config.get("smtp_password") or os.getenv("SMTP_PASSWORD", "")
        self._from = config.get("from_addr") or os.getenv("EMAIL_FROM", "")
        self._to = config.get("to_addr") or os.getenv("EMAIL_TO", "")

    def send(self, message: str, *, parse_mode: str | None = None) -> str:
        """Send ``message`` as an email. Returns a mailto: identifier."""
        if not self._host or not self._from or not self._to:
            raise ValueError(
                "email channel requires smtp_host, from_addr, and to_addr "
                "(set SMTP_HOST, EMAIL_FROM, EMAIL_TO or config)"
            )

        msg = EmailMessage()
        msg["From"] = self._from
        msg["To"] = self._to
        msg["Subject"] = "devflow: approval pending"
        msg.set_content(message)

        with smtplib.SMTP(self._host, self._port, timeout=30) as server:
            server.starttls()
            if self._user and self._password:
                server.login(self._user, self._password)
            server.send_message(msg)

        logger.info("email: sent to %s", self._to)
        return f"mailto:{self._to}"

    def healthcheck(self) -> bool:
        """Return True when host, from, and to are all configured."""
        return bool(self._host and self._from and self._to)
```

- [ ] **Step 4: Register email in the factory**

In `src/devflow/notifications/factory.py`, add `"email"` to `_NOTIFICATION_REGISTRY` (line 28-30):

```python
_NOTIFICATION_REGISTRY: dict[str, type[NotificationChannel]] = {
    "console": ConsoleChannel,
    "email": None,  # placeholder — replaced below via lazy import
}
```

Since `email` uses only stdlib (smtplib), it doesn't need lazy import like httpx channels. Better approach: add it to a new eagerly-imported section. Add at the top of the factory module, after the `ConsoleChannel` import (line 22):

```python
from devflow.notifications.email_channel import EmailChannel
```

And in the registry:

```python
_NOTIFICATION_REGISTRY: dict[str, type[NotificationChannel]] = {
    "console": ConsoleChannel,
    "email": EmailChannel,
}
```

And in the config-dict construction section, add an `elif name == "email"` branch (around line 114):

```python
        elif name == "email":
            config = {
                "smtp_host": os.getenv("SMTP_HOST", extra.get("email", {}).get("smtp_host", "")),
                "smtp_port": int(os.getenv("SMTP_PORT", "587")),
                "smtp_user": os.getenv("SMTP_USER", extra.get("email", {}).get("smtp_user", "")),
                "smtp_password": os.getenv("SMTP_PASSWORD", extra.get("email", {}).get("smtp_password", "")),
                "from_addr": os.getenv("EMAIL_FROM", extra.get("email", {}).get("from_addr", "")),
                "to_addr": os.getenv("EMAIL_TO", extra.get("email", {}).get("to_addr", "")),
            }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/notifications/test_email.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/devflow/notifications/email_channel.py src/devflow/notifications/factory.py tests/unit/notifications/test_email.py
git commit -m "feat(notifications): add SMTP email channel

EmailChannel sends plain-text emails via SMTP with STARTTLS. Reads
SMTP_HOST/PORT/USER/PASSWORD and EMAIL_FROM/TO from env. Registered
in factory as an eager channel (stdlib smtplib, no extra deps)."
```

---

## Task 7: ApprovalBridge — connects interrupt to store + push

**Files:**
- Create: `src/devflow/daemon/approval_bridge.py`
- Test: `tests/unit/daemon/test_approval_bridge.py`

**Interfaces:**
- Consumes: `ApprovalStore` (Task 4), `NotificationChannel` (base.py:9), `Config`/`DaemonConfig` (config.py), `ApprovalCallback` type (graph.py:253)
- Produces: `ApprovalBridge` class. Constructor: `ApprovalBridge(store: ApprovalStore, push_channels: list[NotificationChannel], approval_timeout_hours: int, on_timeout: str)`. Method: `build_callback() -> ApprovalCallback`. The callback registers the payload in the store, sends push notifications, blocks on `store.wait()` with timeout, and on timeout returns a defer/reject decision.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/daemon/test_approval_bridge.py`:

```python
"""Unit tests for the ApprovalBridge."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from devflow.daemon.approval_bridge import ApprovalBridge
from devflow.daemon.approval_store import ApprovalStore


def _make_bridge(store: ApprovalStore | None = None) -> ApprovalBridge:
    """Build an ApprovalBridge with a mock push channel."""
    store = store or ApprovalStore()
    mock_channel = MagicMock()
    mock_channel.send.return_value = "ntfy://ok"
    return ApprovalBridge(
        store=store,
        push_channels=[mock_channel],
        approval_timeout_hours=1,
        on_timeout="defer",
    )


def test_build_callback_returns_callable() -> None:
    """build_callback() returns a callable matching ApprovalCallback."""
    bridge = _make_bridge()
    callback = bridge.build_callback()
    assert callable(callback)


def test_callback_registers_and_sends_push() -> None:
    """The callback registers the payload in the store and sends a push."""
    store = ApprovalStore()
    bridge = _make_bridge(store=store)
    callback = bridge.build_callback()

    payload = {"gate_type": "plan_approval", "task_id": "T-1"}

    # Run the callback in a thread (it blocks on store.wait).
    result_holder: dict = {}

    def run_callback() -> None:
        result_holder["result"] = callback(payload, {"task": None})

    t = threading.Thread(target=run_callback)
    t.start()

    # Wait for the registration to appear.
    for _ in range(20):
        if store.get_pending():
            break
        time.sleep(0.05)

    assert len(store.get_pending()) == 1
    assert store.get_pending()[0]["payload"]["task_id"] == "T-1"

    # Resolve it.
    store.resolve("T-1", {"approved": True, "reason": "ok", "requested_changes": []})

    t.join(timeout=2.0)
    assert result_holder["result"]["approved"] is True


def test_callback_timeout_defer() -> None:
    """On timeout with on_timeout='defer', returns approved=False with reason."""
    store = ApprovalStore()
    bridge = ApprovalBridge(
        store=store,
        push_channels=[],
        approval_timeout_hours=0,  # timeout immediately
        on_timeout="defer",
    )
    callback = bridge.build_callback()

    payload = {"gate_type": "plan_approval", "task_id": "T-timeout"}
    result = callback(payload, {"task": None})

    assert result["approved"] is False
    assert "timeout" in result["reason"].lower()


def test_callback_timeout_reject() -> None:
    """On timeout with on_timeout='reject', returns approved=False with reject reason."""
    store = ApprovalStore()
    bridge = ApprovalBridge(
        store=store,
        push_channels=[],
        approval_timeout_hours=0,
        on_timeout="reject",
    )
    callback = bridge.build_callback()

    payload = {"gate_type": "plan_approval", "task_id": "T-reject"}
    result = callback(payload, {"task": None})

    assert result["approved"] is False
    assert "timeout" in result["reason"].lower()


def test_callback_thread_id_from_payload() -> None:
    """The callback uses task_id from the payload as the thread_id for the store."""
    store = ApprovalStore()
    bridge = _make_bridge(store=store)
    callback = bridge.build_callback()

    payload = {"gate_type": "publish_approval", "task_id": "T-42"}

    def run_and_resolve() -> None:
        # Wait for registration, then resolve.
        for _ in range(20):
            if store.get_pending():
                break
            time.sleep(0.05)
        store.resolve("T-42", {"approved": True, "reason": "", "requested_changes": []})

    resolver = threading.Thread(target=run_and_resolve)
    resolver.start()
    result = callback(payload, {"task": None})
    resolver.join(timeout=2.0)

    assert result["approved"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_approval_bridge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devflow.daemon.approval_bridge'`

- [ ] **Step 3: Write minimal implementation**

Create `src/devflow/daemon/approval_bridge.py`:

```python
"""ApprovalBridge: connects LangGraph interrupt() to the ApprovalStore + push.

The bridge builds an ``ApprovalCallback`` (matching the type alias at
graph.py:253) that:
1. Registers the interrupt payload in the ``ApprovalStore``.
2. Sends push notifications ("approval pending") via configured channels.
3. Blocks on ``store.wait()`` with a timeout.
4. Returns the human decision dict, or a defer/reject decision on timeout.

The ``task_id`` from the interrupt payload is used as the store key
(thread_id), matching how the graph identifies runs.
"""

from __future__ import annotations

import logging
import traceback
from typing import Any, Callable

from devflow.daemon.approval_store import ApprovalStore
from devflow.notifications.base import NotificationChannel
from devflow.state import WorkflowState

logger = logging.getLogger(__name__)

# Type alias matching graph.py:253
ApprovalCallback = Callable[[dict[str, Any], WorkflowState], dict[str, Any]]


class ApprovalBridge:
    """Bridges LangGraph interrupts to the ApprovalStore + push notifications."""

    def __init__(
        self,
        store: ApprovalStore,
        push_channels: list[NotificationChannel],
        approval_timeout_hours: int,
        on_timeout: str,
    ) -> None:
        self._store = store
        self._channels = push_channels
        self._timeout_seconds = approval_timeout_hours * 3600
        self._on_timeout = on_timeout

    def build_callback(self) -> ApprovalCallback:
        """Return an ApprovalCallback for run_workflow_interactive."""

        def callback(payload: dict[str, Any], state: WorkflowState) -> dict[str, Any]:
            task_id = str(payload.get("task_id", "unknown"))
            gate = payload.get("gate_type", "approval")

            logger.info("ApprovalBridge: %s pending for task %s", gate, task_id)

            # 1. Register in store so the API can resolve it.
            self._store.register(task_id, payload)

            # 2. Send push notifications.
            self._send_push(gate, task_id, payload)

            # 3. Block on the store until resolved or timeout.
            decision = self._store.wait(task_id, timeout=self._timeout_seconds)

            if decision is not None:
                logger.info("ApprovalBridge: resolved for task %s", task_id)
                return decision

            # 4. Timeout: defer or reject.
            self._store.remove(task_id)
            logger.warning("ApprovalBridge: timeout for task %s (policy=%s)", task_id, self._on_timeout)
            reason = f"approval timeout (policy: {self._on_timeout})"
            return {
                "approved": False,
                "reason": reason,
                "requested_changes": [],
            }

        return callback

    def _send_push(self, gate: str, task_id: str, payload: dict[str, Any]) -> None:
        """Send a push notification about the pending approval. Best-effort."""
        title = payload.get("task_title", task_id)
        message = (
            f"devflow: {gate} pending\n"
            f"Task: {title} ({task_id})\n"
            f"Review at: http://localhost:8787\n"
        )
        if gate == "publish_approval":
            diff_preview = (payload.get("diff") or "")[:200]
            message += f"Diff preview:\n{diff_preview}\n"

        for channel in self._channels:
            try:
                channel.send(message)
            except Exception:
                logger.warning(
                    "Push channel '%s' failed for approval notification",
                    channel.name,
                    exc_info=True,
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_approval_bridge.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devflow/daemon/approval_bridge.py tests/unit/daemon/test_approval_bridge.py
git commit -m "feat(daemon): add ApprovalBridge connecting interrupt to store

ApprovalBridge.build_callback() returns an ApprovalCallback that
registers the payload in ApprovalStore, sends push via configured
channels, blocks on store.wait with timeout, and returns defer/reject
on timeout."
```

---

## Task 8: Wire ApprovalBridge into WorkflowRunner (interactive mode)

**Files:**
- Modify: `src/devflow/daemon/runner.py:48-103` (run_task)
- Test: `tests/unit/daemon/test_runner.py` (modify)

**Interfaces:**
- Consumes: `ApprovalBridge` (Task 7), `run_workflow_interactive` (graph.py:256), existing `WorkflowRunner`
- Produces: `WorkflowRunner.__init__` gains optional `approval_bridge: ApprovalBridge | None = None`. `run_task` calls `run_workflow_interactive` when a bridge is provided, otherwise falls back to `run_workflow`.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/daemon/test_runner.py`:

```python
def test_run_task_uses_interactive_when_bridge_provided(
    temp_git_repo: Path,
    mock_config: Config,
    fake_llm_factory: Any,
) -> None:
    """When an ApprovalBridge is provided, run_task uses run_workflow_interactive."""
    from unittest.mock import MagicMock
    from devflow.daemon.approval_store import ApprovalStore
    from devflow.daemon.approval_bridge import ApprovalBridge

    # Enable per_plan so the plan_approval interrupt fires.
    mock_config.workflow.hitl_strategy = "per_plan"
    mock_config.workflow.human_in_the_loop = True

    store = ApprovalStore()
    bridge = ApprovalBridge(store=store, push_channels=[], approval_timeout_hours=1, on_timeout="defer")

    bus = EventBus()
    locks = DaemonLocks()
    runner = WorkflowRunner(mock_config, bus, locks, approval_bridge=bridge)

    # Auto-resolve the approval immediately in a background thread.
    import threading, time

    def auto_approve() -> None:
        for _ in range(40):
            pending = store.get_pending()
            if pending:
                tid = pending[0]["thread_id"]
                store.resolve(tid, {"approved": True, "reason": "auto", "requested_changes": []})
                return
            time.sleep(0.05)

    t = threading.Thread(target=auto_approve)
    t.start()

    final_state = runner.run_task(
        task_id="MOCK-1",
        repo_path=str(temp_git_repo),
        thread_id="interactive-runner-test",
    )
    t.join(timeout=2.0)

    assert final_state.get("final_verdict") == FinalVerdict.APPROVE


def test_run_task_falls_back_to_non_interactive_without_bridge(
    temp_git_repo: Path,
    mock_config: Config,
    fake_llm_factory: Any,
) -> None:
    """Without a bridge, run_task uses run_workflow (no interrupt)."""
    bus = EventBus()
    locks = DaemonLocks()
    runner = WorkflowRunner(mock_config, bus, locks)

    final_state = runner.run_task(
        task_id="MOCK-1",
        repo_path=str(temp_git_repo),
        thread_id="non-interactive-test",
    )
    assert final_state.get("final_verdict") == FinalVerdict.APPROVE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_runner.py::test_run_task_uses_interactive_when_bridge_provided tests/unit/daemon/test_runner.py::test_run_task_falls_back_to_non_interactive_without_bridge -v`
Expected: FAIL — `WorkflowRunner.__init__` doesn't accept `approval_bridge`.

- [ ] **Step 3: Modify WorkflowRunner**

In `src/devflow/daemon/runner.py`, add import at the top (after line 24, the `run_workflow` import):

```python
from devflow.graph import run_workflow, run_workflow_interactive
```

Add `ApprovalBridge` import (after the DaemonLocks import):

```python
from devflow.daemon.approval_bridge import ApprovalBridge
```

In `__init__`, add the `approval_bridge` parameter:

```python
    def __init__(
        self,
        app_cfg: Config,
        event_bus: EventBus,
        locks: DaemonLocks,
        task_source: TaskSource | None = None,
        approval_bridge: ApprovalBridge | None = None,
    ) -> None:
        self._cfg = app_cfg
        self._bus = event_bus
        self._locks = locks
        self._task_source = task_source
        self._bridge = approval_bridge
        self.events_published: int = 0
```

In `run_task`, replace the `run_workflow(...)` call with conditional logic:

```python
        try:
            if self._bridge is not None:
                callback = self._bridge.build_callback()
                final_state = run_workflow_interactive(
                    app_cfg=self._cfg,
                    repo_path=repo_path,
                    task_id=task_id,
                    task_source=self._task_source,
                    thread_id=thread_id or task_id,
                    approval_callback=callback,
                )
            else:
                final_state = run_workflow(
                    app_cfg=self._cfg,
                    repo_path=repo_path,
                    task_id=task_id,
                    task_source=self._task_source,
                    thread_id=thread_id or task_id,
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_runner.py -v`
Expected: All tests PASS (both new interactive tests and existing non-interactive ones).

- [ ] **Step 5: Commit**

```bash
git add src/devflow/daemon/runner.py tests/unit/daemon/test_runner.py
git commit -m "feat(daemon): WorkflowRunner uses interactive runner with bridge

run_task now calls run_workflow_interactive when an ApprovalBridge is
provided, falling back to run_workflow otherwise. __init__ gains
optional approval_bridge parameter."
```

---

## Task 9: FastAPI approval endpoints

**Files:**
- Modify: `src/devflow/daemon/web.py` (add approval endpoints)
- Test: `tests/unit/daemon/test_web.py` (modify)

**Interfaces:**
- Consumes: `ApprovalStore` (Task 4), existing `create_app` (web.py:42)
- Produces: `create_app` gains optional `approval_store: ApprovalStore | None = None` parameter. New endpoints: `GET /api/approvals` (list pending), `POST /api/approvals/{thread_id}` (submit decision). `GET /api/health` populates `pending_approvals` count.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/daemon/test_web.py`:

```python
def test_approvals_list_empty() -> None:
    """/api/approvals returns empty list when no approvals pending."""
    from devflow.daemon.approval_store import ApprovalStore

    store = ApprovalStore()
    app, _ = _make_app_with_store(store)
    with TestClient(app) as client:
        resp = client.get("/api/approvals")
    assert resp.status_code == 200
    assert resp.json() == []


def test_approvals_list_shows_pending() -> None:
    """/api/approvals shows registered pending approvals."""
    from devflow.daemon.approval_store import ApprovalStore

    store = ApprovalStore()
    store.register("T-1", {"gate_type": "plan_approval", "task_id": "T-1", "task_title": "Fix bug"})
    app, _ = _make_app_with_store(store)
    with TestClient(app) as client:
        resp = client.get("/api/approvals")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["thread_id"] == "T-1"
    assert data[0]["payload"]["task_title"] == "Fix bug"


def test_approvals_resolve_decision() -> None:
    """POST /api/approvals/{thread_id} delivers a decision to the store."""
    from devflow.daemon.approval_store import ApprovalStore

    store = ApprovalStore()
    store.register("T-1", {"gate_type": "plan_approval", "task_id": "T-1"})
    app, _ = _make_app_with_store(store)
    with TestClient(app) as client:
        resp = client.post(
            "/api/approvals/T-1",
            json={"approved": True, "reason": "looks good", "requested_changes": []},
        )
    assert resp.status_code == 200
    # After resolve, the approval is no longer pending.
    with TestClient(app) as client2:
        resp2 = client2.get("/api/approvals")
    assert resp2.json() == []


def test_approvals_resolve_unknown_returns_404() -> None:
    """POST /api/approvals/{unknown} returns 404."""
    from devflow.daemon.approval_store import ApprovalStore

    store = ApprovalStore()
    app, _ = _make_app_with_store(store)
    with TestClient(app) as client:
        resp = client.post(
            "/api/approvals/nonexistent",
            json={"approved": True, "reason": "", "requested_changes": []},
        )
    assert resp.status_code == 404


def test_health_shows_pending_approvals_count() -> None:
    """/api/health reports the count of pending approvals."""
    from devflow.daemon.approval_store import ApprovalStore

    store = ApprovalStore()
    store.register("T-1", {"task_id": "T-1"})
    store.register("T-2", {"task_id": "T-2"})
    app, _ = _make_app_with_store(store)
    with TestClient(app) as client:
        resp = client.get("/api/health")
    data = resp.json()
    assert data["pending_approvals"] == 2
```

Add the helper at the top of the test file (after imports):

```python
def _make_app_with_store(store):
    """Create a test app wired with an ApprovalStore."""
    from devflow.config import DaemonConfig
    from devflow.daemon.events import EventBus
    from devflow.daemon.locks import DaemonLocks
    from devflow.daemon.web import create_app
    from devflow.config import Config, WorkflowConfig

    cfg = Config(workflow=WorkflowConfig(task_source="mock"), providers={}, agents={})
    cfg.workflow.daemon = DaemonConfig(enabled=True, port=8787)
    bus = EventBus()
    locks = DaemonLocks()
    app = create_app(cfg, locks, bus, runner=None, approval_store=store)
    return app, locks
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_web.py::test_approvals_list_empty -v`
Expected: FAIL — `create_app` doesn't accept `approval_store` or the `/api/approvals` route doesn't exist.

- [ ] **Step 3: Modify create_app to add approval endpoints**

In `src/devflow/daemon/web.py`, add import:

```python
from devflow.daemon.approval_store import ApprovalStore
```

Add a Pydantic model for the decision body:

```python
class ApprovalDecision(BaseModel):
    approved: bool
    reason: str = ""
    requested_changes: list[str] = Field(default_factory=list)
```

Modify `create_app` signature to add `approval_store`:

```python
def create_app(
    app_cfg: Config,
    locks: DaemonLocks,
    event_bus: EventBus,
    runner: Any | None = None,
    approval_store: ApprovalStore | None = None,
) -> FastAPI:
```

Inside `create_app`, after the existing endpoints, add:

```python
    if approval_store is not None:
        @app.get("/api/approvals")
        async def list_approvals() -> list[dict[str, Any]]:
            return approval_store.get_pending()

        @app.post("/api/approvals/{thread_id}")
        async def resolve_approval(thread_id: str, decision: ApprovalDecision):
            resolved = approval_store.resolve(
                thread_id,
                {
                    "approved": decision.approved,
                    "reason": decision.reason,
                    "requested_changes": decision.requested_changes,
                },
            )
            if not resolved:
                from fastapi import HTTPException
                raise HTTPException(status_code=404, detail=f"Unknown thread_id: {thread_id}")
            return {"status": "resolved", "thread_id": thread_id}
```

In the `health()` endpoint, populate `pending_approvals`:

```python
    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        uptime = int(time.monotonic() - start_time)
        task_running = _is_task_running()
        pending = len(approval_store.get_pending()) if approval_store else 0
        return HealthResponse(
            status="degraded" if task_running else "healthy",
            scheduler="busy" if task_running else "running",
            uptime_seconds=uptime,
            current_task=_state.get("current_task"),
            pending_approvals=pending,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_web.py -v`
Expected: All tests PASS (existing + new approval tests).

- [ ] **Step 5: Commit**

```bash
git add src/devflow/daemon/web.py tests/unit/daemon/test_web.py
git commit -m "feat(daemon): add /api/approvals GET+POST endpoints

GET /api/approvals lists pending approvals. POST /api/approvals/{id}
delivers a decision. /api/health now reports pending_approvals count.
create_app gains optional approval_store parameter."
```

---

## Task 10: Wire ApprovalStore + Bridge into daemon startup

**Files:**
- Modify: `src/devflow/daemon/__main__.py:28-81` (run_daemon)
- Test: `tests/unit/daemon/test_main.py` (modify)

**Interfaces:**
- Consumes: `ApprovalStore` (Task 4), `ApprovalBridge` (Task 7), `build_notification_channels` (factory.py:50), `WorkflowRunner(..., approval_bridge=...)`, `create_app(..., approval_store=...)`
- Produces: `run_daemon` now creates ApprovalStore + ApprovalBridge (with ntfy/email channels from config) and wires them into both the runner and the web app.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/daemon/test_main.py`:

```python
def test_run_daemon_wires_approval_store(
    mock_config: Any,
    temp_git_repo: Path,
    fake_llm_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_daemon creates ApprovalStore and passes it to run_web_server."""
    captured: dict = {}

    def fake_web_server(app_cfg, locks, event_bus, runner, approval_store=None):
        captured["approval_store"] = approval_store

    monkeypatch.setattr("devflow.daemon.__main__.run_web_server", fake_web_server)
    monkeypatch.setattr("devflow.daemon.__main__.load_config", lambda *a, **kw: mock_config)
    mock_config.workflow.daemon.enabled = True

    run_daemon(config_dir="config", repo_path=str(temp_git_repo))

    assert captured["approval_store"] is not None
    assert hasattr(captured["approval_store"], "get_pending")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/daemon/test_main.py::test_run_daemon_wires_approval_store -v`
Expected: FAIL — `run_web_server` receives no `approval_store` kwarg.

- [ ] **Step 3: Modify run_daemon**

In `src/devflow/daemon/__main__.py`, add imports:

```python
from devflow.daemon.approval_store import ApprovalStore
from devflow.daemon.approval_bridge import ApprovalBridge
from devflow.notifications.factory import build_notification_channels
```

In `run_daemon`, after creating `event_bus`, `locks`, `runner` (around line 53), add:

```python
    # 3b. Create approval store + bridge for HITL strategies.
    approval_store = ApprovalStore()
    push_channels = build_notification_channels(app_cfg.workflow)
    bridge = ApprovalBridge(
        store=approval_store,
        push_channels=push_channels,
        approval_timeout_hours=daemon_cfg.approval_timeout_hours,
        on_timeout=daemon_cfg.approval_on_timeout,
    )

    # Recreate the runner with the bridge attached.
    runner = WorkflowRunner(app_cfg, event_bus, locks, approval_bridge=bridge)
```

And change the `run_web_server` call to pass the store:

```python
    try:
        run_web_server(app_cfg, locks, event_bus, runner, approval_store=approval_store)
    finally:
        logger.info("Web server stopped; shutting down scheduler...")
        scheduler.shutdown()
        logger.info("Daemon stopped.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/daemon/test_main.py -v`
Expected: All tests PASS (the existing test_run_daemon_starts_components may need its fake_web_server signature updated to accept approval_store=None — check and fix if needed).

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest tests/ -v --ignore=tests/integration`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/devflow/daemon/__main__.py tests/unit/daemon/test_main.py
git commit -m "feat(daemon): wire ApprovalStore + ApprovalBridge into startup

run_daemon now creates ApprovalStore, builds push channels from config,
creates ApprovalBridge, and passes both to WorkflowRunner and the web
server. Existing fake_web_server signatures updated to accept
approval_store kwarg."
```

---

## Task 11: Update config files and .env.example

**Files:**
- Modify: `config/workflow.yaml` (document ntfy/email in corporate_report_channels)
- Modify: `.env.example` (document NTFY_*, SMTP_*, EMAIL_* env vars)

**Interfaces:**
- Consumes: `NtfyChannel` (Task 5), `EmailChannel` (Task 6)

- [ ] **Step 1: Read current config/workflow.yaml**

Read `config/workflow.yaml` to see the current `corporate_report_channels` section.

- [ ] **Step 2: Update config/workflow.yaml**

Find the `corporate_report_channels` section. Add a comment documenting ntfy and email as available channels:

```yaml
corporate_report_channels:
  - console
  # Available channels (Phase 2+):
  # - ntfy    # push notifications via ntfy.sh (needs NTFY_TOPIC env)
  # - email   # SMTP email (needs SMTP_HOST, EMAIL_FROM, EMAIL_TO env)
```

- [ ] **Step 3: Update .env.example**

Append to `.env.example`:

```env
# ── ntfy push notifications ───────────────────────────────────────────────
# NTFY_SERVER=https://ntfy.sh       # or self-hosted URL
# NTFY_TOPIC=devflow-your-team      # topic to publish to
# NTFY_TOKEN=                       # optional, for auth on self-hosted

# ── Email (SMTP) notifications ────────────────────────────────────────────
# SMTP_HOST=smtp.corp.com
# SMTP_PORT=587
# SMTP_USER=
# SMTP_PASSWORD=
# EMAIL_FROM=devflow-bot@corp.com
# EMAIL_TO=dev-team@corp.com
```

- [ ] **Step 4: Verify config loads**

Run: `python -c "from devflow.config import load_config; cfg = load_config('config'); print(cfg.workflow.corporate_report_channels)"`
Expected: Prints `['console']` (the commented channels are not active).

- [ ] **Step 5: Commit**

```bash
git add config/workflow.yaml .env.example
git commit -m "docs: document ntfy + email notification channels in config

workflow.yaml shows available channels (commented out). .env.example
documents NTFY_SERVER/TOPIC/TOKEN and SMTP_HOST/PORT/USER/PASSWORD and
EMAIL_FROM/TO environment variables."
```

---

## Task 12: End-to-end integration test for HITL strategies

**Files:**
- Create: `tests/integration/test_hitl_strategies.py`
- Test: itself

**Interfaces:**
- Consumes: all Phase 2 components

- [ ] **Step 1: Write integration test**

Create `tests/integration/test_hitl_strategies.py`:

```python
"""End-to-end integration tests for HITL strategies.

Tests the full flow: graph build -> interrupt -> bridge -> store -> resolve
for each strategy. Uses fake LLM and mock task source (no real API calls).
"""

from __future__ import annotations

import copy
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from git import Repo

from devflow.config import Config, HitlStrategy
from devflow.daemon.approval_bridge import ApprovalBridge
from devflow.daemon.approval_store import ApprovalStore
from devflow.daemon.events import EventBus
from devflow.daemon.locks import DaemonLocks
from devflow.daemon.runner import WorkflowRunner
from devflow.state import FinalVerdict


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


def _auto_approve_in_bg(store: ApprovalStore) -> threading.Thread:
    """Background thread that auto-approves any pending approval."""
    def _approve() -> None:
        for _ in range(100):
            pending = store.get_pending()
            if pending:
                for entry in pending:
                    store.resolve(
                        entry["thread_id"],
                        {"approved": True, "reason": "auto", "requested_changes": []},
                    )
                return
            time.sleep(0.05)

    t = threading.Thread(target=_approve, daemon=True)
    t.start()
    return t


def test_per_plan_strategy_completes(
    temp_git_repo: Path,
    mock_config: Config,
    fake_llm_factory: Any,
) -> None:
    """per_plan: plan_approval interrupts, publish_approval auto-approves."""
    cfg = copy.deepcopy(mock_config)
    cfg.workflow.hitl_strategy = HitlStrategy.PER_PLAN
    cfg.workflow.human_in_the_loop = True

    store = ApprovalStore()
    bridge = ApprovalBridge(store=store, push_channels=[], approval_timeout_hours=1, on_timeout="defer")
    _auto_approve_in_bg(store)

    runner = WorkflowRunner(cfg, EventBus(), DaemonLocks(), approval_bridge=bridge)
    final_state = runner.run_task("MOCK-1", str(temp_git_repo), "per-plan-e2e")

    assert final_state.get("final_verdict") == FinalVerdict.APPROVE


def test_full_detail_strategy_completes(
    temp_git_repo: Path,
    mock_config: Config,
    fake_llm_factory: Any,
) -> None:
    """full_detail: plan auto-approves, publish_approval interrupts."""
    cfg = copy.deepcopy(mock_config)
    cfg.workflow.hitl_strategy = HitlStrategy.FULL_DETAIL
    cfg.workflow.human_in_the_loop = True

    store = ApprovalStore()
    bridge = ApprovalBridge(store=store, push_channels=[], approval_timeout_hours=1, on_timeout="defer")
    _auto_approve_in_bg(store)

    runner = WorkflowRunner(cfg, EventBus(), DaemonLocks(), approval_bridge=bridge)
    final_state = runner.run_task("MOCK-1", str(temp_git_repo), "full-detail-e2e")

    assert final_state.get("final_verdict") == FinalVerdict.APPROVE


def test_end_of_day_strategy_completes_no_interrupt(
    temp_git_repo: Path,
    mock_config: Config,
    fake_llm_factory: Any,
) -> None:
    """end_of_day: no interrupts at all (both gates auto-approve)."""
    cfg = copy.deepcopy(mock_config)
    cfg.workflow.hitl_strategy = HitlStrategy.END_OF_DAY
    cfg.workflow.human_in_the_loop = True

    store = ApprovalStore()
    bridge = ApprovalBridge(store=store, push_channels=[], approval_timeout_hours=1, on_timeout="defer")

    runner = WorkflowRunner(cfg, EventBus(), DaemonLocks(), approval_bridge=bridge)
    final_state = runner.run_task("MOCK-1", str(temp_git_repo), "eod-e2e")

    assert final_state.get("final_verdict") == FinalVerdict.APPROVE
    # No approval should have been registered (both gates auto-approve).
    assert store.get_pending() == []
```

- [ ] **Step 2: Run the integration tests**

Run: `python -m pytest tests/integration/test_hitl_strategies.py -v`
Expected: All 3 tests PASS. If a test hangs, it means an interrupt fired but wasn't auto-approved (strategy wiring bug).

- [ ] **Step 3: Run FULL test suite**

Run: `python -m pytest tests/ -v`
Expected: ALL tests PASS.

- [ ] **Step 4: Run ruff and mypy**

Run: `ruff check src/devflow/daemon/ src/devflow/nodes/publish_approval.py src/devflow/notifications/ntfy.py src/devflow/notifications/email_channel.py tests/unit/daemon/ tests/unit/nodes/ tests/unit/notifications/ tests/integration/test_hitl_strategies.py`
Expected: No errors.

Run: `mypy src/devflow/daemon/ src/devflow/nodes/publish_approval.py src/devflow/notifications/ntfy.py src/devflow/notifications/email_channel.py`
Expected: No errors.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_hitl_strategies.py
git commit -m "test(integration): end-to-end HITL strategy tests

per_plan: plan interrupts + publish auto-approves. full_detail: plan
auto-approves + publish interrupts. end_of_day: both auto-approve, no
interrupts. All three complete with APPROVE verdict."
```

---

## Self-Review

### Spec coverage (Phase 2 scope)

| Spec section | Task(s) | Covered? |
|---|---|---|
| 3 HITL strategies (per_plan, full_detail, end_of_day) | Tasks 1, 3, 12 | ✅ |
| `publish_approval` node (second gate) | Task 1 | ✅ |
| `publish_approval` wired into graph | Task 2 | ✅ |
| `plan_approval` respects `hitl_strategy` | Task 3 | ✅ |
| ApprovalStore (in-process pending) | Task 4 | ✅ |
| ApprovalBridge (interrupt → store → push → wait) | Task 7 | ✅ |
| ntfy push channel | Task 5 | ✅ |
| email push channel | Task 6 | ✅ |
| WorkflowRunner interactive mode (switch from run_workflow) | Task 8 | ✅ |
| FastAPI `/api/approvals` GET + POST | Task 9 | ✅ |
| `/api/health` pending_approvals count | Task 9 | ✅ |
| Daemon startup wires ApprovalStore + Bridge | Task 10 | ✅ |
| Config + .env documentation | Task 11 | ✅ |
| Approval timeout (defer/reject) | Task 7 (bridge handles timeout) | ✅ |
| E2E integration test for all 3 strategies | Task 12 | ✅ |

**Phase 3-5 items (NOT in this plan, correctly deferred):**
- ForgeBackend (push/MR) → Phase 3
- Reporter refactor (prepare_report + execute_actions) → Phase 3
- EOD batch-store / batch-publish → Phase 4
- Vue SPA / SSE endpoint → Phase 5

### Placeholder scan
Searched for TBD/TODO/FIXME/"implement later"/"similar to" — none found in actionable steps. All code blocks contain complete implementations. (The `TODO(Phase 4)` markers in runner.py/scheduler.py are intentional forward-references from Phase 1, not plan placeholders.)

### Type consistency
- `ApprovalStore.register(thread_id, payload) -> None` — consistent across Task 4 (definition), Task 7 (bridge usage), Task 9 (API usage), Task 10 (startup).
- `ApprovalStore.resolve(thread_id, decision) -> bool` — consistent across Task 4, Task 7, Task 9.
- `ApprovalStore.wait(thread_id, timeout) -> dict | None` — consistent across Task 4, Task 7.
- `ApprovalStore.get_pending() -> list[dict]` — consistent across Task 4, Task 9, Task 10.
- `ApprovalBridge(store, push_channels, approval_timeout_hours, on_timeout)` — consistent across Task 7, Task 8, Task 10, Task 12.
- `ApprovalBridge.build_callback() -> ApprovalCallback` — consistent across Task 7, Task 8.
- `NtfyChannel(config)` / `EmailChannel(config)` — consistent across Tasks 5, 6, and factory registration.
- `create_app(app_cfg, locks, event_bus, runner=None, approval_store=None)` — consistent across Task 9, Task 10.
- `WorkflowRunner(app_cfg, event_bus, locks, task_source=None, approval_bridge=None)` — consistent across Task 8, Task 10, Task 12.
- `publish_approval_node(state, *, app_cfg) -> dict` returning `{"publish_approved": bool, "logs": [...]}` — consistent across Task 1, Task 2.
- Resume value shape `{"approved": bool, "reason": str, "requested_changes": list[str]}` — consistent across plan_approval (existing), publish_approval (Task 1), ApprovalBridge (Task 7), API POST (Task 9).

No type/name mismatches found.
