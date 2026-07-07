"""File-system research source using pathlib."""

from __future__ import annotations

import fnmatch
import logging
from pathlib import Path
from typing import Any

from devflow.research.schemas import ResearchFinding, ResearchRequest
from devflow.research.sources.base import ResearchSource

logger = logging.getLogger(__name__)


class FileSystemSource(ResearchSource):
    """Search the local file system by file name and content."""

    name = "file_system"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.root = Path(self.config.get("root", ".")).resolve()
        self.max_depth = int(self.config.get("max_depth", 5))
        self.max_results = int(self.config.get("max_results", 20))
        self.include_patterns: list[str] = self.config.get("include_patterns", ["*"])
        self.max_content_bytes = int(self.config.get("max_content_bytes", 8_000))

    def healthcheck(self) -> bool:
        """Return True if the configured root exists and is readable."""
        try:
            return self.root.exists() and self.root.is_dir()
        except OSError:
            return False

    def search(self, request: ResearchRequest) -> list[ResearchFinding]:
        """Find files whose names match the query and read their contents."""
        query = request.query
        query_lower = query.lower()
        findings: list[ResearchFinding] = []

        for path in self._walk():
            if len(findings) >= self.max_results:
                break
            if self._matches(query_lower, path):
                findings.append(self._read(path, query))

        return findings

    def _walk(self) -> list[Path]:
        """Collect files under root up to max_depth matching include_patterns."""
        files: list[Path] = []
        for item in self.root.rglob("*"):
            try:
                if not item.is_file():
                    continue
                depth = len(item.relative_to(self.root).parts)
                if depth > self.max_depth:
                    continue
                if not any(fnmatch.fnmatch(item.name, pat) for pat in self.include_patterns):
                    continue
                files.append(item)
            except OSError as exc:
                logger.debug("Skipping %s: %s", item, exc)
        return files

    def _matches(self, query_lower: str, path: Path) -> bool:
        """Return True if the query appears in the file name."""
        return query_lower in path.name.lower()

    def _read(self, path: Path, query: str) -> ResearchFinding:
        """Read the beginning of a file and return a finding."""
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            content = f"<could not read file: {exc}>"

        snippet = content[: self.max_content_bytes]
        if len(content) > self.max_content_bytes:
            snippet = f"{snippet}\n... truncated"

        return ResearchFinding(
            source=self.name,
            query=query,
            title=f"file: {path.relative_to(self.root)}",
            content=snippet,
            uri=f"file://{path}",
            metadata={
                "path": str(path.relative_to(self.root)),
                "size": path.stat().st_size if path.exists() else 0,
                "kind": "file",
            },
        )
