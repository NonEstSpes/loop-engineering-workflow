"""Unit tests for configuration loading."""

from __future__ import annotations

from devflow.config import load_config


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
