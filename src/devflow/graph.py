"""LangGraph state graph construction and workflow runner."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from functools import partial
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Send

from devflow.config import Config
from devflow.mcp.base import TaskSource
from devflow.mcp.factory import build_task_source
from devflow.nodes.checker import aggregate_checker_node, run_checker_node
from devflow.nodes.maker import maker_node
from devflow.nodes.orchestrator import orchestrator_node
from devflow.nodes.prioritizer import prioritizer_node
from devflow.nodes.plan_approval import plan_approval_node
from devflow.nodes.planner import planner_node
from devflow.nodes.publish_approval import publish_approval_node
from devflow.nodes.reporter import reporter_node
from devflow.nodes.research import research_node
from devflow.nodes.self_review import self_review_node
from devflow.nodes.task_fetcher import task_fetcher_node
from devflow.state import FinalVerdict, WorkflowState

logger = logging.getLogger(__name__)

DEFAULT_CHECKERS = ["checker_a", "checker_b", "checker_c"]


def build_graph(
    app_cfg: Config,
    repo_path: str | None = None,
    task_source: TaskSource | None = None,
    task_id: str | None = None,
    checkpointer: Any | None = None,
    queue_store: Any | None = None,
) -> CompiledStateGraph:
    """Build and compile the development workflow state graph."""
    if task_source is None:
        task_source = build_task_source(app_cfg.workflow)

    if checkpointer is None:
        checkpointer = InMemorySaver()

    base_branch = app_cfg.workflow.default_branch

    graph = StateGraph(WorkflowState)

    # Prioritizer node — LLM-evaluates TASKS.md and writes the execution queue.
    # Only added when a queue_store is provided (daemon mode).
    if queue_store is not None:
        graph.add_node(
            "prioritizer",
            partial(
                prioritizer_node,
                app_cfg=app_cfg,
                repo_path=repo_path or ".",
                queue_store=queue_store,
            ),
        )

    graph.add_node(
        "orchestrator",
        partial(
            orchestrator_node,
            app_cfg=app_cfg,
            todo_path=app_cfg.workflow.todo_path,
            task_source=task_source,
            task_id=task_id,
        ),
    )
    graph.add_node(
        "task_fetcher",
        partial(task_fetcher_node, task_source=task_source, task_id=task_id),
    )
    graph.add_node(
        "planner",
        partial(planner_node, app_cfg=app_cfg, repo_path=repo_path),
    )
    graph.add_node(
        "plan_approval",
        partial(plan_approval_node, app_cfg=app_cfg),
    )
    graph.add_node(
        "maker",
        partial(
            maker_node,
            app_cfg=app_cfg,
            repo_path=repo_path or ".",
            base_branch=base_branch,
        ),
    )
    graph.add_node(
        "self_review",
        partial(self_review_node, app_cfg=app_cfg),
    )
    graph.add_node(
        "run_checker",
        partial(run_checker_node, app_cfg=app_cfg),
    )
    graph.add_node(
        "aggregate_checker",
        partial(aggregate_checker_node, app_cfg=app_cfg),
    )
    graph.add_node(
        "publish_approval",
        partial(publish_approval_node, app_cfg=app_cfg),
    )
    graph.add_node(
        "reporter",
        partial(reporter_node, app_cfg=app_cfg),
    )
    graph.add_node(
        "research",
        partial(research_node, app_cfg=app_cfg),
    )

    # Entry edge: prioritizer → orchestrator (when queue_store provided),
    # otherwise START → orchestrator directly.
    if queue_store is not None:
        graph.add_edge(START, "prioritizer")
        graph.add_edge("prioritizer", "orchestrator")
    else:
        graph.add_edge(START, "orchestrator")
    # Short-circuit to the reporter on orchestrator errors (no actionable
    # task, TODO read failure, hydration failure) so we do not waste a
    # planner LLM call on a stale/empty state.
    graph.add_conditional_edges(
        "orchestrator",
        _after_orchestrator,
        {"task_fetcher": "task_fetcher", "reporter": "reporter"},
    )
    graph.add_edge("task_fetcher", "planner")
    graph.add_edge("planner", "plan_approval")
    graph.add_conditional_edges(
        "plan_approval",
        _after_plan_approval,
        {"maker": "maker", "reporter": "reporter"},
    )
    graph.add_edge("maker", "self_review")
    graph.add_conditional_edges(
        "self_review",
        _after_self_review,
        {"run_checker": "run_checker", "reporter": "reporter"},
    )
    graph.add_edge("run_checker", "aggregate_checker")
    graph.add_conditional_edges(
        "aggregate_checker",
        partial(_after_aggregate_checker, app_cfg=app_cfg),
        {
            "maker": "maker",
            "publish_approval": "publish_approval",
            "reporter": "reporter",
        },
    )
    graph.add_conditional_edges(
        "research",
        _after_research,
        {
            "planner": "planner",
            "maker": "maker",
            "self_review": "self_review",
            "run_checker": "run_checker",
        },
    )
    # publish_approval -> reporter on approve, END on reject/error. The
    # conditional edge (instead of an unconditional add_edge) ensures a human
    # rejection skips the reporter so the work is NOT published.
    graph.add_conditional_edges(
        "publish_approval",
        _after_publish_approval,
        {"reporter": "reporter", END: END},
    )
    graph.add_edge("reporter", END)

    return graph.compile(checkpointer=checkpointer)


def _after_orchestrator(state: WorkflowState) -> str:
    """Route after the orchestrator: reporter on error, task_fetcher otherwise."""
    if state.get("error"):
        logger.info("Routing orchestrator -> reporter due to error")
        return "reporter"
    return "task_fetcher"


def _after_plan_approval(state: WorkflowState) -> str:
    """Route after plan approval: maker on approval, reporter otherwise."""
    if state.get("error"):
        logger.info("Routing plan_approval -> reporter due to error")
        return "reporter"
    if state.get("plan_approved"):
        return "maker"
    logger.info("Routing plan_approval -> reporter because plan was rejected")
    return "reporter"


def _after_publish_approval(state: WorkflowState) -> str:
    """Route after publish approval: reporter on approve, END on reject."""
    if state.get("error"):
        logger.info("Routing publish_approval -> END due to error")
        return END
    if state.get("publish_approved"):
        return "reporter"
    logger.info("Routing publish_approval -> END (rejected by human)")
    return END


def _after_self_review(state: WorkflowState) -> list[Send] | str:
    """Route after self-review: dispatch checkers, or reporter on error."""
    if state.get("error"):
        logger.info("Routing self_review -> reporter due to error")
        return "reporter"

    task = state.get("task")
    diff = state.get("diff")
    self_review_notes = state.get("self_review_notes")
    if task is None or diff is None:
        return "reporter"

    return [
        Send(
            "run_checker",
            {
                "checker_agent": name,
                "task": task,
                "diff": diff,
                "self_review_notes": self_review_notes,
            },
        )
        for name in DEFAULT_CHECKERS
    ]


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


def _after_research(state: WorkflowState) -> str:
    """Route after research back to the node that requested it."""
    result = state.get("last_research_result")
    caller = result.caller if result is not None else "reporter"
    logger.info("Routing research -> %s", caller)
    return caller


def run_workflow(
    app_cfg: Config,
    repo_path: str | None = None,
    task_id: str | None = None,
    task_source: TaskSource | None = None,
    initial_state: WorkflowState | None = None,
    thread_id: str | None = None,
    queue_store: Any | None = None,
) -> WorkflowState:
    """Build the graph and run it to completion."""
    graph = build_graph(
        app_cfg=app_cfg,
        repo_path=repo_path,
        task_source=task_source,
        task_id=task_id,
        queue_store=queue_store,
    )
    config: RunnableConfig = {
        "configurable": {"thread_id": thread_id or str(uuid.uuid4())},
    }
    return cast(WorkflowState, graph.invoke(initial_state or {}, config))


#: Type of the human-approval callback used by ``run_workflow_interactive``.
#:
#: Receives the interrupt payload and the current workflow state, and must
#: return the resume value expected by the paused node (e.g. a dict
#: ``{"approved": bool, "reason": str, "requested_changes": list[str]}`` for
#: plan approval).
ApprovalCallback = Callable[[dict[str, Any], WorkflowState], dict[str, Any]]


def run_workflow_interactive(
    app_cfg: Config,
    repo_path: str | None = None,
    task_id: str | None = None,
    task_source: TaskSource | None = None,
    initial_state: WorkflowState | None = None,
    thread_id: str | None = None,
    approval_callback: ApprovalCallback | None = None,
    *,
    max_resumptions: int = 50,
    queue_store: Any | None = None,
) -> WorkflowState:
    """Run the workflow, resuming from ``interrupt()`` pauses via a callback.

    Unlike :func:`run_workflow`, this runner detects when the graph pauses on an
    ``interrupt()`` (currently plan approval), asks the ``approval_callback`` to
    produce a resume value, and continues execution. This is the consumer side
    of the human-in-the-loop gate that ``plan_approval`` raises.

    The graph is built with the default :class:`InMemorySaver` checkpointer and
    a stable ``thread_id`` so the paused state survives between ``invoke``
    calls within a single process.

    The callback signature is ``(payload, state) -> resume_value`` where
    ``payload`` is the dict passed to ``interrupt()``. If ``approval_callback``
    is ``None`` or the interrupt payload is not recognised, the current state is
    returned immediately (the graph stays paused).
    """
    graph = build_graph(
        app_cfg=app_cfg,
        repo_path=repo_path,
        task_source=task_source,
        task_id=task_id,
        queue_store=queue_store,
    )
    config: RunnableConfig = {
        "configurable": {"thread_id": thread_id or str(uuid.uuid4())},
    }

    # Initial run: proceeds until END or the first interrupt.
    result = cast(WorkflowState, graph.invoke(initial_state or {}, config))

    for _ in range(max_resumptions):
        snapshot = graph.get_state(config)
        # No pending tasks -> the graph reached END.
        if snapshot is None or not snapshot.next:
            break

        interrupts = snapshot.interrupts
        if not interrupts:
            # Paused for a reason other than interrupt; nothing we can resume.
            logger.warning("Graph paused without an interrupt; returning current state")
            break

        interrupt_obj = interrupts[0]
        payload = interrupt_obj.value
        logger.info("Workflow paused on interrupt; payload keys: %s", list(payload))

        if approval_callback is None:
            logger.info("No approval callback; returning paused state")
            break

        resume_value = approval_callback(payload, result)
        logger.info("Resuming workflow with human decision")
        result = cast(WorkflowState, graph.invoke(Command(resume=resume_value), config))

    return result
