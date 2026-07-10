"""Unit tests for configuration loading."""

from __future__ import annotations

from pathlib import Path

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
