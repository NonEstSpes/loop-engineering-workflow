"""Tests for the file_system research driver."""

from __future__ import annotations

from pathlib import Path

from devflow.research.schemas import ResearchRequest
from devflow.research.sources.file_system import FileSystemSource


def test_file_system_finds_file_by_name(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Hello\n")
    source = FileSystemSource({"root": str(tmp_path)})
    findings = source.search(ResearchRequest(query="readme"))
    assert len(findings) == 1
    assert findings[0].source == "file_system"
    assert "README.md" in findings[0].title
    assert "# Hello" in findings[0].content


def test_file_system_respects_max_results(tmp_path: Path) -> None:
    for i in range(5):
        (tmp_path / f"file{i}.txt").write_text(f"content {i}")
    source = FileSystemSource({"root": str(tmp_path), "max_results": 2})
    findings = source.search(ResearchRequest(query="file"))
    assert len(findings) == 2


def test_file_system_respects_include_patterns(tmp_path: Path) -> None:
    (tmp_path / "keep.py").write_text("x = 1\n")
    (tmp_path / "skip.log").write_text("log line\n")
    source = FileSystemSource({"root": str(tmp_path), "include_patterns": ["*.py"]})
    findings = source.search(ResearchRequest(query="skip"))
    assert findings == []


def test_file_system_missing_root() -> None:
    source = FileSystemSource({"root": "/nonexistent/path/12345"})
    assert source.healthcheck() is False
    assert source.search(ResearchRequest(query="anything")) == []
