"""LangGraph state graph construction and workflow runner."""

from __future__ import annotations

import logging
import uuid
from functools import partial
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from devflow.config import Config
from devflow.mcp.base import TaskSource
from devflow.mcp.factory import build_task_source
from devflow.nodes.checker import aggregate_checker_node, run_checker_node
from devflow.nodes.maker import maker_node
from devflow.nodes.orchestrator import orchestrator_node
from devflow.nodes.plan_approval import plan_approval_node
from devflow.nodes.planner import planner_node
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
) -> CompiledStateGraph:
    """Build and compile the development workflow state graph."""
    if task_source is None:
        task_source = build_task_source(app_cfg.workflow)

    if checkpointer is None:
        checkpointer = InMemorySaver()

    base_branch = app_cfg.workflow.default_branch

    graph = StateGraph(WorkflowState)

    graph.add_node(
        "orchestrator",
        partial(orchestrator_node, app_cfg=app_cfg),
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
        "reporter",
        partial(reporter_node, app_cfg=app_cfg),
    )
    graph.add_node(
        "research",
        partial(research_node, app_cfg=app_cfg),
    )

    graph.add_edge(START, "orchestrator")
    graph.add_edge("orchestrator", "task_fetcher")
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
        {"maker": "maker", "reporter": "reporter"},
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
    graph.add_edge("reporter", END)

    return graph.compile(checkpointer=checkpointer)


def _after_plan_approval(state: WorkflowState) -> str:
    """Route after plan approval: maker on approval, reporter otherwise."""
    if state.get("error"):
        logger.info("Routing plan_approval -> reporter due to error")
        return "reporter"
    if state.get("plan_approved"):
        return "maker"
    logger.info("Routing plan_approval -> reporter because plan was rejected")
    return "reporter"


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
    """Route after checker aggregation: maker for rework, reporter otherwise."""
    if state.get("error"):
        logger.info("Routing aggregate_checker -> reporter due to error")
        return "reporter"

    verdict = state.get("final_verdict")
    rework_count = state.get("rework_count", 0)
    max_rework = app_cfg.workflow.max_rework_iterations

    if verdict == FinalVerdict.APPROVE:
        return "reporter"

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
) -> WorkflowState:
    """Build the graph and run it to completion."""
    graph = build_graph(
        app_cfg=app_cfg,
        repo_path=repo_path,
        task_source=task_source,
        task_id=task_id,
    )
    config: RunnableConfig = {
        "configurable": {"thread_id": thread_id or str(uuid.uuid4())},
    }
    return cast(WorkflowState, graph.invoke(initial_state or {}, config))
