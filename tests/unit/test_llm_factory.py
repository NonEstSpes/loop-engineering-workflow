"""Unit tests for the LLM factory."""

from __future__ import annotations

import pytest
from langchain_openai import ChatOpenAI

from devflow.config import AgentConfig, Config, ProviderConfig
from devflow.llm_factory import build_llm, list_supported_providers


def test_list_supported_providers_includes_kimi() -> None:
    """Kimi is advertised as a supported provider."""
    providers = list_supported_providers()
    assert "kimi" in providers
    assert "openai" in providers


def test_build_llm_uses_chat_openai_for_kimi(mock_config: Config) -> None:
    """The 'kimi' provider is backed by ChatOpenAI with the Moonshot base URL."""
    mock_config.providers["kimi"] = ProviderConfig(
        name="kimi",
        api_key="fake-kimi-key",
        base_url="https://api.moonshot.cn/v1",
    )
    agent_cfg = AgentConfig(
        name="planner",
        provider="kimi",
        model="kimi-latest",
        system_prompt="You are a planner.",
    )

    llm = build_llm(agent_cfg, mock_config)

    assert isinstance(llm, ChatOpenAI)
    assert llm.openai_api_key.get_secret_value() == "fake-kimi-key"
    assert str(llm.openai_api_base) == "https://api.moonshot.cn/v1"
    assert llm.model_name == "kimi-latest"


def test_build_llm_uses_chat_openai_for_moonshot_alias(mock_config: Config) -> None:
    """The 'moonshot' alias is also backed by ChatOpenAI."""
    mock_config.providers["moonshot"] = ProviderConfig(
        name="moonshot",
        api_key="fake-moonshot-key",
        base_url="https://api.moonshot.cn/v1",
    )
    agent_cfg = AgentConfig(
        name="planner",
        provider="moonshot",
        model="kimi-latest",
        system_prompt="You are a planner.",
    )

    llm = build_llm(agent_cfg, mock_config)

    assert isinstance(llm, ChatOpenAI)


def test_build_llm_falls_back_to_default_base_url_for_kimi(mock_config: Config) -> None:
    """If no base_url is configured for Kimi, the OpenAI default is used."""
    mock_config.providers["kimi"] = ProviderConfig(name="kimi", api_key="fake-key")
    agent_cfg = AgentConfig(
        name="planner",
        provider="kimi",
        model="kimi-latest",
        system_prompt="You are a planner.",
    )

    llm = build_llm(agent_cfg, mock_config)

    assert isinstance(llm, ChatOpenAI)


def test_build_llm_rejects_unknown_provider(mock_config: Config) -> None:
    """A configured but unsupported provider raises a clear error."""
    mock_config.providers["unknown-provider"] = ProviderConfig(name="unknown-provider")
    agent_cfg = AgentConfig(
        name="planner",
        provider="unknown-provider",
        model="unknown-model",
        system_prompt="You are a planner.",
    )

    with pytest.raises(ValueError, match="Unsupported LLM provider"):
        build_llm(agent_cfg, mock_config)
