"""Factory for building TaskSource adapters from workflow configuration."""

from __future__ import annotations

import os
import shlex
from typing import Any

from devflow.config import WorkflowConfig
from devflow.mcp.base import TaskSource
from devflow.mcp.jira import JiraTaskSource
from devflow.mcp.mock import MockTaskSource
from devflow.mcp.redmine import RedmineTaskSource

_TASK_SOURCE_REGISTRY: dict[str, type[TaskSource]] = {
    "mock": MockTaskSource,
    "redmine": RedmineTaskSource,
    "jira": JiraTaskSource,
}


def build_task_source(
    workflow_cfg: WorkflowConfig,
    extra: dict[str, Any] | None = None,
) -> TaskSource:
    """Build a TaskSource from the workflow config and optional extra values."""
    extra = extra or {}
    name = workflow_cfg.task_source
    cls = _TASK_SOURCE_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"Unknown task source '{name}'. Supported: {list(_TASK_SOURCE_REGISTRY)}")

    config: dict[str, Any]
    if name == "redmine":
        url = os.getenv("REDMINE_URL", extra.get("url", ""))
        api_key = os.getenv("REDMINE_API_KEY", extra.get("api_key", ""))
        config = {
            "url": url,
            "api_key": api_key,
            "host_header": os.getenv("REDMINE_HOST_HEADER", extra.get("host_header", "")),
        }
        # Allow overriding how the Redmine MCP server is launched. ``extra``
        # takes precedence over env, and an explicit ``server`` key wins over
        # both (used by tests to inject a mock client config).
        if "server" in extra:
            config["server"] = extra["server"]
        else:
            command = os.getenv("REDMINE_MCP_COMMAND", extra.get("command", "uvx"))
            args_env = os.getenv("REDMINE_MCP_ARGS", extra.get("args", "--from mcp-redmine mcp-redmine"))
            server_env: dict[str, str] = {
                "REDMINE_URL": url,
                "REDMINE_API_KEY": api_key,
            }
            host_header = config["host_header"]
            if host_header:
                server_env["REDMINE_HOST_HEADER"] = host_header
            config["server"] = {
                "transport": "stdio",
                "command": command,
                "args": shlex.split(args_env),
                "env": server_env,
            }
    elif name == "jira":
        config = {
            "url": os.getenv("JIRA_URL", extra.get("url", "")),
            "username": os.getenv("JIRA_USERNAME", extra.get("username", "")),
            "api_token": os.getenv("JIRA_API_TOKEN", extra.get("api_token", "")),
        }
    else:
        config = extra

    return cls(config)


def register_task_source(name: str, cls: type[TaskSource]) -> None:
    """Register a custom task source adapter."""
    if not issubclass(cls, TaskSource):
        raise TypeError(f"{cls} must be a subclass of TaskSource")
    _TASK_SOURCE_REGISTRY[name] = cls
