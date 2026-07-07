"""Tests for the on-demand research node."""

from __future__ import annotations

from typing import Any

from devflow.config import ResearchSourceConfig, ResearchSourcesConfig
from devflow.nodes.research import (
    format_research_context,
    request_research_command,
    research_node,
    take_research_result,
)
from devflow.research.factory import SourceFactory
from devflow.research.schemas import ResearchFinding, ResearchRequest, ResearchResult
from devflow.research.sources.base import ResearchSource


class _StubSource(ResearchSource):
    """Test source that returns a fixed finding."""

    name = "stub"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.query = config.get("query", "")

    def search(self, request: ResearchRequest) -> list[ResearchFinding]:
        return [
            ResearchFinding(
                source=self.name,
                query=request.query,
                title="stub result",
                content=f"answer for {request.query}",
            )
        ]


def _make_config(enabled: bool = True) -> ResearchSourcesConfig:
    return ResearchSourcesConfig(
        request_human_clarification=False,
        max_research_calls_per_node=2,
        sources=[
            ResearchSourceConfig(
                name="stub",
                driver="stub",
                enabled=enabled,
                config={},
            )
        ],
    )


def test_research_node_returns_findings() -> None:
    """research_node aggregates findings and updates state."""
    from devflow.config import Config

    cfg = Config(
        workflow={"task_source": "mock"},  # type: ignore[arg-type]
        providers={},
        agents={},
        research_sources=_make_config(),
    )
    factory = SourceFactory()
    factory.register("stub", _StubSource)

    state: dict[str, Any] = {
        "research_request": ResearchRequest(query="test", caller="planner"),
        "research_call_count": 0,
    }
    update = research_node(state, app_cfg=cfg, source_factory=factory)

    assert update["research_request"] is None
    assert update["research_call_count"] == 1
    result = update["last_research_result"]
    assert isinstance(result, ResearchResult)
    assert result.caller == "planner"
    assert len(result.findings) == 1
    assert result.findings[0].content == "answer for test"


def test_research_node_respects_budget() -> None:
    """research_node stops when the call budget is exhausted."""
    from devflow.config import Config

    cfg = Config(
        workflow={"task_source": "mock"},  # type: ignore[arg-type]
        providers={},
        agents={},
        research_sources=_make_config(),
    )

    state: dict[str, Any] = {
        "research_request": ResearchRequest(query="test", caller="planner"),
        "research_call_count": 2,
    }
    update = research_node(state, app_cfg=cfg, source_factory=SourceFactory())

    result = update["last_research_result"]
    assert result is not None
    assert "budget exhausted" in result.summary


def test_take_research_result_filters_by_caller() -> None:
    """take_research_result only returns results for the matching caller."""
    result = ResearchResult(query="q", caller="planner")
    state: dict[str, Any] = {"last_research_result": result}
    assert take_research_result(state, "planner") is result
    assert take_research_result(state, "maker") is None


def test_format_research_context() -> None:
    """format_research_context produces a readable summary."""
    result = ResearchResult(query="q", summary="summary text")
    text = format_research_context(result)
    assert "Prior research result" in text
    assert "summary text" in text


def test_request_research_command() -> None:
    """request_research_command produces a Command to the research node."""
    request = ResearchRequest(query="q", caller="maker")
    command = request_research_command(request)
    assert command.goto == "research"
    assert command.update["research_request"] == request
