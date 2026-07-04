"""Unit tests for the code manipulation tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from devflow.tools.code_tools import CodeTools


@pytest.fixture
def tools(temp_dir: Path) -> CodeTools:
    """Return a CodeTools instance scoped to a temp directory."""
    return CodeTools(temp_dir)


def test_write_and_read_file(tools: CodeTools, temp_dir: Path) -> None:
    """Files can be written and read back."""
    tools.write_file("src/hello.py", "print('hello')")
    assert (temp_dir / "src/hello.py").read_text(encoding="utf-8") == "print('hello')"
    assert tools.read_file("src/hello.py") == "print('hello')"


def test_edit_file(tools: CodeTools) -> None:
    """edit_file replaces the first occurrence of a string."""
    tools.write_file("data.txt", "foo bar baz")
    tools.edit_file("data.txt", "bar", "qux")
    assert tools.read_file("data.txt") == "foo qux baz"


def test_list_tree(tools: CodeTools) -> None:
    """list_tree returns sorted relative file paths."""
    tools.write_file("a.txt", "a")
    tools.write_file("sub/b.txt", "b")
    files = tools.list_tree()
    assert files == ["a.txt", "sub/b.txt"]


def test_run_command(tools: CodeTools) -> None:
    """Commands run in the working directory and return output."""
    output = tools.run_command(["python", "-c", "print(2 + 2)"])
    assert "[exit 0]" in output
    assert "4" in output


def test_path_escaping(tools: CodeTools, temp_dir: Path) -> None:
    """Paths that escape the working directory are rejected."""
    with pytest.raises(ValueError, match="escapes"):
        tools.read_file("../outside.txt")
