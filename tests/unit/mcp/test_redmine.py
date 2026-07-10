"""Unit tests for the Redmine task source adapter."""

from __future__ import annotations

from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

from devflow.mcp.redmine import RedmineTaskSource


def _make_result(text: str) -> CallToolResult:
    """Build a minimal MCP CallToolResult carrying a text blob."""
    return CallToolResult(content=[TextContent(type="text", text=text)])


class FakeMcpClient:
    """Records calls and returns canned YAML/JSON for ``redmine_request``."""

    def __init__(self, server_config: dict[str, Any]) -> None:
        self.server_config = server_config
        self.calls: list[dict[str, Any]] = []
        # Map (method, path) -> response text. Set by the test.
        self.responses: dict[tuple[str, str], str] = {}
        self.closed = False

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> CallToolResult:
        assert name == "redmine_request"
        arguments = arguments or {}
        self.calls.append(dict(arguments))
        key = (arguments.get("method", ""), arguments.get("path", ""))
        text = self.responses.get(key, "")
        return _make_result(text)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> FakeMcpClient:
    """Replace McpClient in the redmine module with a recording fake."""
    instance = FakeMcpClient({})

    def _fake_init(self: Any, server_config: dict[str, Any]) -> None:
        self.server_config = server_config

    # McpClient stores server_config in __init__; we give the fake the same
    # shape, then swap the methods the adapter actually uses.
    monkeypatch.setattr(
        "devflow.mcp.redmine.McpClient",
        lambda server_config: _bind_fake(instance, server_config),
    )
    return instance


def _bind_fake(instance: FakeMcpClient, server_config: dict[str, Any]) -> FakeMcpClient:
    instance.server_config = server_config
    return instance


def _base_config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "url": "https://redmine.example.com",
        "api_key": "secret-key",
    }
    config.update(overrides)
    return config


# ---------------------------------------------------------------------------
# __init__ validation
# ---------------------------------------------------------------------------


def test_init_requires_url() -> None:
    with pytest.raises(ValueError, match="url"):
        RedmineTaskSource({"url": "", "api_key": "k"})


def test_init_requires_api_key() -> None:
    with pytest.raises(ValueError, match="api_key"):
        RedmineTaskSource({"url": "https://redmine.example.com", "api_key": ""})


def test_init_builds_default_server_config(fake_client: FakeMcpClient) -> None:
    RedmineTaskSource(_base_config())
    assert fake_client.server_config["transport"] == "stdio"
    assert fake_client.server_config["command"] == "uvx"
    assert fake_client.server_config["args"] == ["--from", "mcp-redmine", "mcp-redmine"]
    assert fake_client.server_config["env"]["REDMINE_URL"] == "https://redmine.example.com"
    assert fake_client.server_config["env"]["REDMINE_API_KEY"] == "secret-key"


def test_init_accepts_explicit_server_config(fake_client: FakeMcpClient) -> None:
    custom = {"transport": "stdio", "command": "echo", "args": [], "env": {}}
    source = RedmineTaskSource(_base_config(server=custom))
    # The explicit server config is used verbatim (and host_header stays empty).
    assert fake_client.server_config is custom
    assert source.host_header == ""


def test_init_includes_host_header_in_env(fake_client: FakeMcpClient) -> None:
    RedmineTaskSource(_base_config(host_header="redmine.local"))
    assert fake_client.server_config["env"]["REDMINE_HOST_HEADER"] == "redmine.local"


# ---------------------------------------------------------------------------
# fetch_tasks
# ---------------------------------------------------------------------------

ISSUES_YAML = """
issues:
  - id: 101
    subject: Add login endpoint
    description: Implement /login returning a JWT.
    status:
      name: New
    project:
      name: web-app
      id: 5
    tracker:
      name: Feature
    priority:
      name: High
    assigned_to:
      name: Alice
    author:
      name: Bob
    created_on: "2026-07-01T10:00:00Z"
    updated_on: "2026-07-05T12:00:00Z"
    done_ratio: 0
  - id: 102
    subject: Fix typo in docs
    description: README has a typo.
    status:
      name: New
    project:
      name: docs
      id: 9
"""


def test_fetch_tasks_parses_issues(fake_client: FakeMcpClient) -> None:
    fake_client.responses[("GET", "/issues.json")] = ISSUES_YAML
    source = RedmineTaskSource(_base_config())

    tasks = source.fetch_tasks(status="open", limit=50)

    assert len(tasks) == 2
    first = tasks[0]
    assert first.id == "101"
    assert first.title == "Add login endpoint"
    assert first.description == "Implement /login returning a JWT."
    assert first.status == "New"
    # The call used the right filter for "open" tasks assigned to current user.
    call = fake_client.calls[0]
    assert call["params"]["assigned_to_id"] == "me"
    assert call["params"]["status_id"] == "open"
    assert call["params"]["limit"] == "50"


def test_fetch_tasks_empty_when_no_issues(fake_client: FakeMcpClient) -> None:
    fake_client.responses[("GET", "/issues.json")] = "issues: []\n"
    source = RedmineTaskSource(_base_config())

    assert source.fetch_tasks() == []


def test_fetch_tasks_empty_response(fake_client: FakeMcpClient) -> None:
    fake_client.responses[("GET", "/issues.json")] = ""
    source = RedmineTaskSource(_base_config())

    assert source.fetch_tasks() == []


# ---------------------------------------------------------------------------
# get_task_details
# ---------------------------------------------------------------------------

ISSUE_YAML = """
issue:
  id: 123
  subject: Fix login bug
  description: Users cannot log in.
  status:
    name: In Progress
  project:
    name: web-app
    id: 5
  tracker:
    name: Bug
  priority:
    name: Urgent
"""


def test_get_task_details(fake_client: FakeMcpClient) -> None:
    fake_client.responses[("GET", "/issues/123.json")] = ISSUE_YAML
    source = RedmineTaskSource(_base_config())

    task = source.get_task_details("123")

    assert task.id == "123"
    assert task.title == "Fix login bug"
    assert task.description == "Users cannot log in."
    assert task.status == "In Progress"


def test_get_task_details_not_found(fake_client: FakeMcpClient) -> None:
    fake_client.responses[("GET", "/issues/999.json")] = ""
    source = RedmineTaskSource(_base_config())

    with pytest.raises(ValueError, match="999"):
        source.get_task_details("999")


# ---------------------------------------------------------------------------
# update_task_status
# ---------------------------------------------------------------------------

STATUSES_YAML = """
issue_statuses:
  - id: 1
    name: New
  - id: 2
    name: In Progress
  - id: 3
    name: Resolved
  - id: 5
    name: Closed
  - id: 6
    name: Rejected
  - id: 7
    name: Feedback
"""


def test_update_task_status_calls_put_with_resolved_id(
    fake_client: FakeMcpClient,
) -> None:
    fake_client.responses[("GET", "/issue_statuses.json")] = STATUSES_YAML
    fake_client.responses[("PUT", "/issues/123.json")] = ""
    source = RedmineTaskSource(_base_config())

    source.update_task_status("123", "resolved", comment="Final verdict: approve")

    # First call loads statuses; second is the actual PUT.
    put_call = fake_client.calls[-1]
    assert put_call["method"] == "PUT"
    assert put_call["path"] == "/issues/123.json"
    assert put_call["data"] == {
        "issue": {
            "status_id": 3,  # Resolved -> id 3
            "notes": "Final verdict: approve",
        }
    }


def test_update_task_status_rejected_maps_to_rejected_id(
    fake_client: FakeMcpClient,
) -> None:
    fake_client.responses[("GET", "/issue_statuses.json")] = STATUSES_YAML
    fake_client.responses[("PUT", "/issues/7.json")] = ""
    source = RedmineTaskSource(_base_config())

    source.update_task_status("7", "rejected")

    put_call = fake_client.calls[-1]
    assert put_call["data"]["issue"]["status_id"] == 6  # Rejected -> id 6
    # No comment -> no notes field.
    assert "notes" not in put_call["data"]["issue"]


def test_update_task_status_pending_maps_to_feedback(
    fake_client: FakeMcpClient,
) -> None:
    fake_client.responses[("GET", "/issue_statuses.json")] = STATUSES_YAML
    fake_client.responses[("PUT", "/issues/9.json")] = ""
    source = RedmineTaskSource(_base_config())

    source.update_task_status("9", "pending")

    put_call = fake_client.calls[-1]
    assert put_call["data"]["issue"]["status_id"] == 7  # Feedback -> id 7


def test_update_task_status_unresolved_name_passed_through(
    fake_client: FakeMcpClient,
) -> None:
    # Statuses loaded but no match -> name returned unchanged.
    fake_client.responses[("GET", "/issue_statuses.json")] = STATUSES_YAML
    fake_client.responses[("PUT", "/issues/1.json")] = ""
    source = RedmineTaskSource(_base_config())

    source.update_task_status("1", "weird-status")

    put_call = fake_client.calls[-1]
    assert put_call["data"]["issue"]["status_id"] == "weird-status"


def test_status_resolution_is_cached(fake_client: FakeMcpClient) -> None:
    fake_client.responses[("GET", "/issue_statuses.json")] = STATUSES_YAML
    fake_client.responses[("GET", "/issues.json")] = "issues: []\n"
    fake_client.responses[("PUT", "/issues/1.json")] = ""
    source = RedmineTaskSource(_base_config())

    # Two updates with a verdict status (triggers status resolution both times).
    source.update_task_status("1", "resolved")
    source.update_task_status("1", "rejected")

    # /issue_statuses.json should be requested only once.
    status_calls = [c for c in fake_client.calls if c["path"] == "/issue_statuses.json"]
    assert len(status_calls) == 1


# ---------------------------------------------------------------------------
# _parse_issue rich metadata
# ---------------------------------------------------------------------------


def test_parse_issue_rich_metadata(fake_client: FakeMcpClient) -> None:
    source = RedmineTaskSource(_base_config())
    issue = {
        "id": 777,
        "subject": "Refactor config loader",
        "description": "Move configuration loading.",
        "status": {"name": "New"},
        "project": {"name": "infra", "id": 12},
        "tracker": {"name": "Task"},
        "priority": {"name": "Normal"},
        "assigned_to": {"name": "Alice"},
        "author": {"name": "Bob"},
        "created_on": "2026-06-01T00:00:00Z",
        "updated_on": "2026-07-01T00:00:00Z",
        "done_ratio": 30,
    }

    task = source._parse_issue(issue)  # noqa: SLF001

    assert task.id == "777"
    assert task.title == "Refactor config loader"
    assert task.status == "New"
    md = task.metadata
    assert md["project"] == "infra"
    assert md["project_id"] == 12
    assert md["tracker"] == "Task"
    assert md["priority"] == "Normal"
    assert md["assigned_to"] == "Alice"
    assert md["author"] == "Bob"
    assert md["created_on"] == "2026-06-01T00:00:00Z"
    assert md["updated_on"] == "2026-07-01T00:00:00Z"
    assert md["done_ratio"] == 30
    assert md["redmine_url"] == "https://redmine.example.com/issues/777"


def test_parse_issue_handles_missing_fields(fake_client: FakeMcpClient) -> None:
    source = RedmineTaskSource(_base_config())
    task = source._parse_issue({"id": 1})  # noqa: SLF001

    assert task.id == "1"
    assert task.title == ""
    assert task.description == ""
    assert task.status == "open"
    # Metadata keys present but None.
    assert task.metadata["project"] is None
    assert task.metadata["redmine_url"] == "https://redmine.example.com/issues/1"


def test_url_trailing_slash_stripped(fake_client: FakeMcpClient) -> None:
    source = RedmineTaskSource(_base_config(url="https://redmine.example.com/"))
    task = source._parse_issue({"id": 42})  # noqa: SLF001
    assert task.metadata["redmine_url"] == "https://redmine.example.com/issues/42"


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


def test_close_delegates_to_client(fake_client: FakeMcpClient) -> None:
    source = RedmineTaskSource(_base_config())
    source.close()
    assert fake_client.closed is True
