"""Load agent and provider configuration from markdown/YAML files."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import frontmatter
import yaml
from pydantic import BaseModel, Field, ValidationError


class AgentConfig(BaseModel):
    """Configuration for a single agent/subagent."""

    name: str
    provider: str
    model: str
    temperature: float = 0.3
    max_tokens: int | None = None
    system_prompt: str
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    auto_approve: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)


class ProviderConfig(BaseModel):
    """Configuration for an LLM provider."""

    name: str
    type: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    api_version: str | None = None
    timeout: int | None = None
    max_retries: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class WorkflowConfig(BaseModel):
    """Top-level workflow wiring configuration."""

    task_source: str
    max_rework_iterations: int = 3
    human_in_the_loop: bool = True
    default_branch: str = "main"
    pr_target_branch: str = "main"
    corporate_report_channels: list[str] = Field(default_factory=list)
    # Channels used by the daemon to push "approval pending" notifications.
    # Separate from corporate_report_channels so enabling ntfy (or any
    # push channel) for HITL approvals does NOT also route all corporate
    # reports to that channel. Empty by default (no push notifications).
    approval_push_channels: list[str] = Field(default_factory=list)
    # Path to the TODO.md file that the orchestrator reads task entries from
    # and the reporter writes completion results back to. May be overridden by
    # the --todo-path CLI flag or the DEVFLOW_TODO_PATH env variable.
    todo_path: str = "TODO.md"
    # Human-in-the-loop cadence for the daemon: per_plan (each plan), full_detail
    # (each step), or end_of_day (batch at EOD). See HitlStrategy for valid values.
    # May be overridden by the DEVFLOW_HITL_STRATEGY env variable.
    hitl_strategy: str = "per_plan"
    daemon: DaemonConfig = Field(default_factory=lambda: DaemonConfig())


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


class ResearchSourceConfig(BaseModel):
    """Configuration for a single research source driver."""

    name: str
    driver: str
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class ResearchSourcesConfig(BaseModel):
    """Aggregated research source configuration."""

    request_human_clarification: bool = False
    max_research_calls_per_node: int = 3
    sources: list[ResearchSourceConfig] = Field(default_factory=list)


class Config(BaseModel):
    """Aggregated application configuration."""

    workflow: WorkflowConfig
    providers: dict[str, ProviderConfig]
    agents: dict[str, AgentConfig]
    research_sources: ResearchSourcesConfig = Field(default_factory=ResearchSourcesConfig)


def load_agent_config(path: Path) -> AgentConfig:
    """Load a single agent config from a markdown file with YAML frontmatter."""
    post = frontmatter.load(str(path))
    metadata = post.metadata or {}
    metadata.setdefault("name", path.stem)
    metadata["system_prompt"] = post.content
    try:
        return AgentConfig.model_validate(metadata)
    except ValidationError as exc:
        raise ValueError(f"Invalid agent config: {path}") from exc


def load_agents(agents_dir: Path) -> dict[str, AgentConfig]:
    """Load all agent configs from a directory."""
    agents: dict[str, AgentConfig] = {}
    if not agents_dir.exists():
        return agents
    override_provider = os.getenv("DEVFLOW_PROVIDER_OVERRIDE")
    override_model = os.getenv("DEVFLOW_MODEL_OVERRIDE")
    override_temperature = os.getenv("DEVFLOW_TEMPERATURE_OVERRIDE")
    for file_path in sorted(agents_dir.glob("*.md")):
        cfg = load_agent_config(file_path)
        if override_provider:
            cfg.provider = override_provider
        if override_model:
            cfg.model = override_model
        if override_temperature:
            try:
                cfg.temperature = float(override_temperature)
            except ValueError as exc:
                raise ValueError(
                    f"DEVFLOW_TEMPERATURE_OVERRIDE must be a float, got {override_temperature!r}"
                ) from exc
        agents[cfg.name] = cfg
    return agents


def _resolve_env(value: Any) -> Any:
    """Replace ${ENV_VAR} placeholders with environment variable values."""
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        env_name = value[2:-1]
        # Support ${VAR:-default}
        if ":-" in env_name:
            env_name, default = env_name.split(":-", 1)
            return os.getenv(env_name, default)
        return os.getenv(env_name, "")
    return value


def _resolve_env_recursive(obj: Any) -> Any:
    """Recursively resolve environment variables in a nested structure."""
    if isinstance(obj, dict):
        return {k: _resolve_env_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_recursive(v) for v in obj]
    return _resolve_env(obj)


def load_providers(path: Path) -> dict[str, ProviderConfig]:
    """Load provider configs from a YAML file with environment interpolation."""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    raw = _resolve_env_recursive(raw)
    providers_raw = (
        raw["providers"] if isinstance(raw.get("providers"), dict) else raw
    )
    providers: dict[str, ProviderConfig] = {}
    for name, data in providers_raw.items():
        data["name"] = name
        try:
            providers[name] = ProviderConfig.model_validate(data)
        except ValidationError as exc:
            raise ValueError(f"Invalid provider config: {name}") from exc
    return providers


def load_workflow_config(path: Path) -> WorkflowConfig:
    """Load top-level workflow config from a YAML file."""
    if not path.exists():
        return WorkflowConfig(task_source="mock")
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    raw = _resolve_env_recursive(raw)
    # Allow one-shot overrides for CI / mock runs
    hil_override = os.getenv("DEVFLOW_HUMAN_IN_THE_LOOP")
    if hil_override is not None:
        raw["human_in_the_loop"] = hil_override.lower() in {"true", "1", "yes"}
    branch_override = os.getenv("DEVFLOW_DEFAULT_BRANCH")
    if branch_override:
        raw["default_branch"] = branch_override
    todo_override = os.getenv("DEVFLOW_TODO_PATH")
    if todo_override:
        raw["todo_path"] = todo_override
    hitl_override = os.getenv("DEVFLOW_HITL_STRATEGY")
    if hitl_override:
        if hitl_override not in HitlStrategy.ALL:
            raise ValueError(
                f"DEVFLOW_HITL_STRATEGY must be one of {sorted(HitlStrategy.ALL)}, "
                f"got {hitl_override!r}"
            )
        raw["hitl_strategy"] = hitl_override
    try:
        return WorkflowConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid workflow config: {path}") from exc


def load_research_sources(path: Path) -> ResearchSourcesConfig:
    """Load research source configs from a YAML file with environment interpolation."""
    if not path.exists():
        return ResearchSourcesConfig()
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    raw = _resolve_env_recursive(raw)
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid research sources config: {path}")
    try:
        return ResearchSourcesConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid research sources config: {path}") from exc


def load_config(config_dir: str = "config") -> Config:
    """Load full configuration from the config directory."""
    config_path = Path(config_dir)

    workflow = load_workflow_config(config_path / "workflow.yaml")
    providers = load_providers(config_path / "providers.yaml")
    agents = load_agents(config_path / "agents")
    research_sources = load_research_sources(config_path / "research_sources.yaml")

    if not agents:
        raise ValueError(f"No agent configs found in {config_path / 'agents'}")

    return Config(
        workflow=workflow,
        providers=providers,
        agents=agents,
        research_sources=research_sources,
    )
