"""Factory for building LangChain chat models from provider configs."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from devflow.config import AgentConfig, Config


def _build_llm_impl(agent_cfg: AgentConfig, app_cfg: Config) -> BaseChatModel:
    """Actual provider-specific LLM construction."""
    provider_cfg = app_cfg.providers.get(agent_cfg.provider)
    if provider_cfg is None:
        raise ValueError(
            f"Agent '{agent_cfg.name}' references unknown provider '{agent_cfg.provider}'. "
            f"Available providers: {list(app_cfg.providers.keys())}"
        )

    params: dict[str, Any] = {
        "model": agent_cfg.model,
        "temperature": agent_cfg.temperature,
    }
    if agent_cfg.max_tokens is not None:
        params["max_tokens"] = agent_cfg.max_tokens

    # Merge provider-level extras
    for key, value in provider_cfg.extra.items():
        params.setdefault(key, value)

    api_key = provider_cfg.api_key
    base_url = provider_cfg.base_url
    api_version = provider_cfg.api_version

    provider = provider_cfg.name.lower()

    if provider in {"openai", "kimi", "moonshot"}:
        from langchain_openai import ChatOpenAI

        if api_key:
            params["api_key"] = api_key
        if base_url:
            params["base_url"] = base_url
        return ChatOpenAI(**params)

    if provider in {"anthropic"}:
        from langchain_anthropic import ChatAnthropic

        if api_key:
            params["api_key"] = api_key
        if base_url:
            params["anthropic_api_url"] = base_url
        return ChatAnthropic(**params)

    if provider in {"google", "google_genai", "google-genai"}:
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError as exc:
            raise ImportError(
                "Install optional dependency [google] to use Google GenAI provider."
            ) from exc
        if api_key:
            params["google_api_key"] = api_key
        return ChatGoogleGenerativeAI(**params)

    if provider in {"azure", "azure_openai", "azure-openai"}:
        from langchain_openai import AzureChatOpenAI

        if api_key:
            params["api_key"] = api_key
        if base_url:
            params["azure_endpoint"] = base_url
        if api_version:
            params["api_version"] = api_version
        return AzureChatOpenAI(**params)

    if provider in {"ollama"}:
        from langchain_community.chat_models import ChatOllama

        if base_url:
            params["base_url"] = base_url
        return ChatOllama(**params)

    raise ValueError(f"Unsupported LLM provider: {provider}")


def build_llm(agent_cfg: AgentConfig, app_cfg: Config) -> BaseChatModel:
    """Create a LangChain chat model for an agent using its provider config.

    This thin wrapper delegates to _build_llm_impl so tests can swap the
    implementation without fighting import-time name binding.
    """
    return _build_llm_impl(agent_cfg, app_cfg)


def list_supported_providers() -> list[str]:
    """Return the list of provider names known to the factory."""
    return ["openai", "kimi", "anthropic", "google", "azure", "ollama"]
