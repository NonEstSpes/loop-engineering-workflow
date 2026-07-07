"""Unit tests for research source configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from devflow.config import ResearchSourcesConfig, load_research_sources


def test_load_research_sources_missing_file() -> None:
    """A missing sources file yields an empty configuration."""
    cfg = load_research_sources(Path("nonexistent.yaml"))
    assert cfg == ResearchSourcesConfig()


def test_load_research_sources_loads_sources(tmp_path: Path) -> None:
    """Sources are loaded from YAML and validated."""
    path = tmp_path / "research_sources.yaml"
    path.write_text(
        "request_human_clarification: true\n"
        "max_research_calls_per_node: 5\n"
        "sources:\n"
        "  - name: web\n"
        "    driver: web_search\n"
        "    enabled: true\n"
        "    config:\n"
        "      timeout: 15\n"
        "      engine: duckduckgo\n",
        encoding="utf-8",
    )
    cfg = load_research_sources(path)
    assert cfg.request_human_clarification is True
    assert cfg.max_research_calls_per_node == 5
    assert len(cfg.sources) == 1
    source = cfg.sources[0]
    assert source.name == "web"
    assert source.driver == "web_search"
    assert source.enabled is True
    assert source.config == {"timeout": 15, "engine": "duckduckgo"}


def test_load_research_sources_env_interpolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Environment placeholders in source config are resolved."""
    monkeypatch.setenv("TEST_ENGINE", "test-engine")
    path = tmp_path / "research_sources.yaml"
    path.write_text(
        "sources:\n"
        "  - name: web\n"
        "    driver: web_search\n"
        "    config:\n"
        "      engine: ${TEST_ENGINE}\n",
        encoding="utf-8",
    )
    cfg = load_research_sources(path)
    assert cfg.sources[0].config["engine"] == "test-engine"


def test_load_research_sources_invalid_source(tmp_path: Path) -> None:
    """Invalid source data raises a descriptive error."""
    path = tmp_path / "research_sources.yaml"
    path.write_text(
        "sources:\n"
        "  - name: bad\n"
        "    enabled: not_a_bool\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Invalid research sources config"):
        load_research_sources(path)
