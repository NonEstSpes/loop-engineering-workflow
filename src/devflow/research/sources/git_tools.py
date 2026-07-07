"""Git-based research source using grep, log, and show."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

from devflow.research.schemas import ResearchFinding, ResearchRequest
from devflow.research.sources.base import ResearchSource

logger = logging.getLogger(__name__)


class GitToolsSource(ResearchSource):
    """Search a local git repository with git grep, log, and show."""

    name = "git_tools"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.repo_path = Path(self.config.get("repo_path", ".")).resolve()
        self.max_log_results = int(self.config.get("max_log_results", 20))
        self.max_grep_results = int(self.config.get("max_grep_results", 50))

    def _run(self, args: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
        """Run a git command in the configured repository."""
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=check,
        )

    def healthcheck(self) -> bool:
        """Return True if repo_path is inside a git repository."""
        result = self._run(["rev-parse", "--git-dir"])
        return result.returncode == 0

    def search(self, request: ResearchRequest) -> list[ResearchFinding]:
        """Search commits and file contents for the query."""
        query = request.query
        findings: list[ResearchFinding] = []
        findings.extend(self._grep(query))
        findings.extend(self._log(query))
        return findings

    def _grep(self, query: str) -> list[ResearchFinding]:
        """Run git grep and return matching lines."""
        result = self._run(["grep", "-n", "--", query])
        if result.returncode != 0 or not result.stdout.strip():
            return []

        findings: list[ResearchFinding] = []
        for line in result.stdout.splitlines()[: self.max_grep_results]:
            if ":" not in line:
                continue
            path, rest = line.split(":", 1)
            line_no, _, content = rest.partition(":")
            findings.append(
                ResearchFinding(
                    source=self.name,
                    query=query,
                    title=f"git grep: {path}:{line_no}",
                    content=content.strip(),
                    uri=f"file://{self.repo_path / path}",
                    metadata={
                        "path": path,
                        "line": line_no,
                        "kind": "grep",
                    },
                )
            )
        return findings

    def _log(self, query: str) -> list[ResearchFinding]:
        """Run git log --grep and return matching commits."""
        result = self._run(
            [
                "log",
                f"--max-count={self.max_log_results}",
                "--format=%H%x00%s",
                "--grep",
                query,
            ]
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []

        findings: list[ResearchFinding] = []
        for line in result.stdout.splitlines():
            if "\x00" not in line:
                continue
            commit_hash, subject = line.split("\x00", 1)
            findings.append(
                ResearchFinding(
                    source=self.name,
                    query=query,
                    title=f"git log: {subject}",
                    content=subject,
                    uri=f"file://{self.repo_path}",
                    metadata={
                        "commit": commit_hash,
                        "kind": "log",
                    },
                )
            )
        return findings
