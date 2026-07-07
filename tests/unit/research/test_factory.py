"""Tests for the research source factory."""

from __future__ import annotations

from devflow.config import ResearchSourceConfig, ResearchSourcesConfig
from devflow.research.factory import SourceFactory
from devflow.research.sources.git_tools import GitToolsSource


def test_default_factory_registers_builtin_drivers() -> None:
    factory = SourceFactory.default()
    assert factory.is_registered("git_tools")
    assert factory.is_registered("file_system")
    assert factory.is_registered("web_search")
    assert factory.is_registered("graphify_mcp")
    assert factory.is_registered("mcp_generic")


def test_factory_builds_enabled_sources_only() -> None:
    factory = SourceFactory.default()
    cfg = ResearchSourcesConfig(
        sources=[
            ResearchSourceConfig(name="git", driver="git_tools", enabled=True, config={}),
            ResearchSourceConfig(name="fs", driver="file_system", enabled=False, config={}),
            ResearchSourceConfig(name="unknown", driver="missing", enabled=True, config={}),
        ]
    )
    sources = factory.build(cfg)
    assert list(sources.keys()) == ["git"]
    assert isinstance(sources["git"], GitToolsSource)


def test_factory_ignores_unregistered_driver() -> None:
    factory = SourceFactory()
    cfg = ResearchSourcesConfig(
        sources=[ResearchSourceConfig(name="git", driver="git_tools", enabled=True, config={})]
    )
    assert factory.build(cfg) == {}


def test_factory_registers_custom_driver() -> None:
    factory = SourceFactory()
    factory.register("git_tools", GitToolsSource)
    cfg = ResearchSourcesConfig(
        sources=[ResearchSourceConfig(name="git", driver="git_tools", enabled=True, config={})]
    )
    sources = factory.build(cfg)
    assert "git" in sources
    assert isinstance(sources["git"], GitToolsSource)
