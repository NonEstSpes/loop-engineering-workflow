"""Abstract base class and result model for research sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from devflow.research.schemas import ResearchFinding, ResearchRequest


class ResearchSource(ABC):
    """Driver that can run a research request and return findings."""

    name: str = "base"

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    @abstractmethod
    def search(self, request: ResearchRequest) -> list[ResearchFinding]:
        """Run a request against this source and return findings."""

    def healthcheck(self) -> bool:
        """Return True if the source appears usable."""
        return True

    def close(self) -> None:
        """Release any resources held by the driver."""
        return None
