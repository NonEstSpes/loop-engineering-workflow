"""Orchestrator node: initialize state and route to planning."""

from __future__ import annotations

import logging
from typing import Any

from devflow.config import Config
from devflow.state import WorkflowState

logger = logging.getLogger(__name__)


def orchestrator_node(state: WorkflowState, *, app_cfg: Config) -> dict[str, Any]:
    """Initialize workflow state and log the incoming task."""
    task = state.get("task")
    task_id = task.id if task else "<none>"
    logger.info("Orchestrator received task %s", task_id)
    return {
        "rework_count": 0,
        "checker_reports": [],
        "logs": [f"orchestrator: received task {task_id}"],
    }
