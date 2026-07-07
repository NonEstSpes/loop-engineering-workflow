"""Web search research source with an optional duckduckgo-search backend."""

from __future__ import annotations

import logging
from typing import Any

from devflow.research.schemas import ResearchFinding, ResearchRequest
from devflow.research.sources.base import ResearchSource

logger = logging.getLogger(__name__)


try:
    from duckduckgo_search import DDGS
except ImportError:  # pragma: no cover - optional dependency
    DDGS = None  # type: ignore[misc,assignment]


class WebSearchSource(ResearchSource):
    """Run web queries, preferring duckduckgo-search when installed."""

    name = "web_search"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.max_results = int(self.config.get("max_results", 5))
        self.timeout = int(self.config.get("timeout", 30))

    def healthcheck(self) -> bool:
        """Return True only when the duckduckgo-search backend is available."""
        return DDGS is not None

    def search(self, request: ResearchRequest) -> list[ResearchFinding]:
        """Return web results for the query."""
        query = request.query
        if DDGS is None:
            logger.info(
                "duckduckgo-search is not installed; returning stubbed web search result"
            )
            return [
                ResearchFinding(
                    source=self.name,
                    query=query,
                    title="Web search stub",
                    content=(
                        "Web search is unavailable because duckduckgo-search is not "
                        "installed. Install it to enable real web search."
                    ),
                    metadata={"stubbed": True},
                )
            ]

        findings: list[ResearchFinding] = []
        try:
            with DDGS() as ddgs:
                results = ddgs.text(
                    query,
                    max_results=self.max_results,
                )
                for result in results:
                    findings.append(
                        ResearchFinding(
                            source=self.name,
                            query=query,
                            title=result.get("title", "Untitled"),
                            content=result.get("body", ""),
                            uri=result.get("href"),
                            metadata={"kind": "web"},
                        )
                    )
        except Exception as exc:  # pragma: no cover - network dependency
            logger.warning("Web search failed: %s", exc)

        return findings
