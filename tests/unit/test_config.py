"""Unit tests for configuration loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from devflow.config import load_config, load_providers


def test_load_config_uses_temp_dir(mock_config: object, temp_dir: object) -> None:
    """`load_config` reads agents, providers, and workflow from the config dir."""
    cfg = load_config(str(temp_dir))
    assert "orchestrator" in cfg.agents
    assert "planner" in cfg.agents
    assert "maker" in cfg.agents
    assert "mock" in cfg.providers
    assert cfg.workflow.task_source == "mock"


def test_mock_provider_available(mock_config: object) -> None:
    """The mock provider is available for tests."""
    assert "mock" in mock_config.providers
    assert mock_config.providers["mock"].name == "mock"


def test_plan_approval_auto_approved(mock_config: object) -> None:
    """The mock config auto-approves plans to avoid HITL in tests."""
    assert mock_config.agents["plan_approval"].auto_approve is True


def test_load_providers_parses_type_timeout_retries(temp_dir: Path) -> None:
    """The new providers schema exposes type, timeout, and max_retries."""
    (temp_dir / "providers.yaml").write_text(
        "providers:\n"
        "  kimi:\n"
        "    type: openai_compatible\n"
        "    api_key: fake-key\n"
        "    base_url: https://api.moonshot.cn/v1\n"
        "    timeout: 120\n"
        "    max_retries: 3\n"
        "  local:\n"
        "    type: openai_compatible\n"
        "    api_key: dummy\n"
        "    base_url: http://localhost:11434/v1\n"
        "    timeout: 180\n"
        "    max_retries: 2\n",
        encoding="utf-8",
    )
    providers = load_providers(temp_dir / "providers.yaml")
    assert providers["kimi"].type == "openai_compatible"
    assert providers["kimi"].timeout == 120
    assert providers["kimi"].max_retries == 3
    assert providers["local"].type == "openai_compatible"
    assert providers["local"].timeout == 180
    assert providers["local"].max_retries == 2


def test_load_providers_supports_legacy_flat_format(temp_dir: Path) -> None:
    """Provider files without a top-level `providers:` key still load."""
    (temp_dir / "providers.yaml").write_text(
        "kimi:\n"
        "  api_key: fake-key\n"
        "  base_url: https://api.moonshot.cn/v1\n",
        encoding="utf-8",
    )
    providers = load_providers(temp_dir / "providers.yaml")
    assert providers["kimi"].name == "kimi"
    assert providers["kimi"].api_key == "fake-key"


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


def test_hitl_strategy_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DEVFLOW_HITL_STRATEGY env var overrides the YAML value."""
    from devflow.config import load_workflow_config

    yaml_path = tmp_path / "workflow.yaml"
    yaml_path.write_text(
        "task_source: mock\nhitl_strategy: per_plan\n", encoding="utf-8"
    )
    monkeypatch.setenv("DEVFLOW_HITL_STRATEGY", "end_of_day")
    cfg = load_workflow_config(yaml_path)
    assert cfg.hitl_strategy == "end_of_day"


def test_workflow_config_has_forge_defaults() -> None:
    """WorkflowConfig gets sensible forge defaults when not specified."""
    from devflow.config import WorkflowConfig

    cfg = WorkflowConfig(task_source="mock")
    assert cfg.forge.provider == "none"
    assert cfg.forge.target_branch == "main"
    assert cfg.forge.actions == ["publish_report", "update_tracker", "record_todo"]


def test_forge_config_from_yaml(tmp_path: Path) -> None:
    """Forge config loads from YAML."""
    from devflow.config import load_workflow_config

    yaml_path = tmp_path / "workflow.yaml"
    yaml_path.write_text(
        "task_source: mock\n"
        "forge:\n"
        "  provider: github\n"
        "  target_branch: develop\n"
        "  actions:\n"
        "    - publish_report\n"
        "    - update_tracker\n"
        "    - record_todo\n"
        "    - push\n"
        "    - create_mr\n",
        encoding="utf-8",
    )
    cfg = load_workflow_config(yaml_path)
    assert cfg.forge.provider == "github"
    assert cfg.forge.target_branch == "develop"
    assert "push" in cfg.forge.actions
    assert "create_mr" in cfg.forge.actions


def test_daemon_config_has_frontend_defaults() -> None:
    """DaemonConfig gets frontend defaults."""
    from devflow.config import DaemonConfig

    cfg = DaemonConfig()
    assert cfg.serve_frontend is True
    assert cfg.frontend_dist == "frontend/dist"
    assert cfg.cors_origins == []


def test_daemon_config_frontend_from_yaml(tmp_path: Path) -> None:
    """Frontend config loads from YAML."""
    from devflow.config import load_workflow_config

    yaml_path = tmp_path / "workflow.yaml"
    yaml_path.write_text(
        "task_source: mock\n"
        "daemon:\n"
        "  enabled: true\n"
        "  cors_origins:\n"
        "    - http://localhost:5173\n"
        "  frontend_dist: build/spa\n",
        encoding="utf-8",
    )
    cfg = load_workflow_config(yaml_path)
    assert cfg.daemon.cors_origins == ["http://localhost:5173"]
    assert cfg.daemon.frontend_dist == "build/spa"
