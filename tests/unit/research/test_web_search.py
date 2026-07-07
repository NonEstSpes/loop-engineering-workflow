"""Tests for the web_search research driver."""

from __future__ import annotations

from devflow.research.schemas import ResearchRequest
from devflow.research.sources.web_search import DDGS, WebSearchSource


def test_web_search_healthcheck_without_dependency() -> None:
    source = WebSearchSource({"max_results": 3})
    if DDGS is None:
        assert source.healthcheck() is False
    else:
        assert source.healthcheck() is True


def test_web_search_returns_stub_when_dependency_missing() -> None:
    source = WebSearchSource({"max_results": 3})
    if DDGS is None:
        findings = source.search(ResearchRequest(query="python"))
        assert len(findings) == 1
        assert findings[0].metadata.get("stubbed") is True
        assert "duckduckgo-search" in findings[0].content
