"""Code manipulation tools used by the maker agent."""

from __future__ import annotations

import fnmatch
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class CodeTools:
    """Filesystem and command tools scoped to a working directory."""

    def __init__(self, work_dir: Path | str) -> None:
        self.work_dir = Path(work_dir).resolve()
        if not self.work_dir.exists():
            raise FileNotFoundError(f"Working directory does not exist: {self.work_dir}")

    def _resolve(self, rel_path: str) -> Path:
        """Resolve a path relative to work_dir and ensure it stays inside."""
        target = (self.work_dir / rel_path).resolve()
        if self.work_dir not in target.parents and target != self.work_dir:
            raise ValueError(f"Path escapes working directory: {rel_path}")
        return target

    def read_file(self, rel_path: str) -> str:
        """Read a text file from the working directory."""
        path = self._resolve(rel_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        return path.read_text(encoding="utf-8")

    def write_file(self, rel_path: str, content: str) -> None:
        """Write content to a file, creating parent directories if needed."""
        path = self._resolve(rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        logger.info("Wrote %s", path)

    def edit_file(self, rel_path: str, old_string: str, new_string: str) -> None:
        """Replace the first occurrence of old_string with new_string in a file."""
        path = self._resolve(rel_path)
        content = path.read_text(encoding="utf-8")
        if old_string not in content:
            raise ValueError(f"old_string not found in {rel_path}")
        content = content.replace(old_string, new_string, 1)
        path.write_text(content, encoding="utf-8")
        logger.info("Edited %s", path)

    def list_tree(
        self,
        rel_path: str = ".",
        ignore_patterns: list[str] | None = None,
    ) -> list[str]:
        """List files recursively, optionally ignoring patterns."""
        root = self._resolve(rel_path)
        ignore_patterns = ignore_patterns or [
            ".git",
            "__pycache__",
            "*.pyc",
            ".venv",
            "node_modules",
        ]
        files: list[str] = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(self.work_dir).as_posix()
            parts = rel.split("/")
            if any(
                fnmatch.fnmatch(rel, pat)
                or fnmatch.fnmatch(path.name, pat)
                or any(fnmatch.fnmatch(part, pat) for part in parts)
                for pat in ignore_patterns
            ):
                continue
            files.append(rel)
        return sorted(files)

    def search_files(
        self,
        pattern: str,
        glob: str = "**/*",
    ) -> dict[str, list[tuple[int, str]]]:
        """Search for a substring in files matching glob."""
        matches: dict[str, list[tuple[int, str]]] = {}
        for path in self.work_dir.glob(glob):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            file_matches: list[tuple[int, str]] = []
            for lineno, line in enumerate(text.splitlines(), start=1):
                if pattern in line:
                    file_matches.append((lineno, line.strip()))
            if file_matches:
                rel = path.relative_to(self.work_dir).as_posix()
                matches[rel] = file_matches
        return matches

    def run_command(
        self,
        command: list[str],
        cwd: str | None = None,
        check: bool = True,
    ) -> str:
        """Run a shell command in the working directory or a subdirectory."""
        run_dir = self._resolve(cwd) if cwd else self.work_dir
        logger.debug("Running command: %s in %s", " ".join(command), run_dir)
        result = subprocess.run(
            command,
            cwd=run_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        output = f"[exit {result.returncode}]\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        if check and result.returncode != 0:
            raise RuntimeError(f"Command failed: {command}\n{output}")
        return output

    def path_exists(self, rel_path: str) -> bool:
        """Check if a path exists inside the working directory."""
        return self._resolve(rel_path).exists()
