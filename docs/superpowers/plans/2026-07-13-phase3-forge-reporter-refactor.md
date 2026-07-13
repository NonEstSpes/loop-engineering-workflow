# Phase 3: ForgeBackend + Reporter Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `ForgeBackend` abstraction with GitHub and GitLab implementations (push branch + create MR), and refactor the reporter node so publication actions (publish_report, update_tracker, push, create_mr) are config-driven and gated by the HITL strategy — replacing the current hardcoded placeholder URL and always-publish behavior.

**Architecture:** A new `src/devflow/forge/` module mirrors the existing `notifications/` and `mcp/` factory pattern: abstract `ForgeBackend` base, `GitHubBackend` and `GitLabBackend` implementations, `build_forge_backend` factory with registry. The reporter node splits into `prepare_report` (LLM, always runs) and `execute_actions` (deterministic, strategy-gated). Push uses `GitPython` (`repo.remotes.origin.push`); MR uses forge REST API via `httpx`. Conventions-skill markdown files (`config/conventions/*.md`) are loaded into the reporter's system prompt so teams customize commit/MR/report formats without code changes.

**Tech Stack:** Python ≥3.11, GitPython (existing), httpx (existing `web` extra), Pydantic (existing), LangGraph (existing), pytest (existing).

## Global Constraints

- Python ≥3.11 (pyproject.toml line 10)
- Line length 100, ruff rules: E, F, I, W, UP, B, C4, SIM (pyproject.toml lines 52-56)
- `asyncio_mode = "auto"` for pytest-asyncio (pyproject.toml line 67)
- `testpaths = ["tests"]`, `pythonpath = ["src"]` (pyproject.toml lines 68-69)
- All code and logs in English
- Factory pattern: registry dict + build function + `register_*` hook (see `mcp/factory.py` and `notifications/factory.py`)
- `HitlStrategy` plain string-constant class (config.py:67-74): `PER_PLAN`, `FULL_DETAIL`, `END_OF_DAY`
- `DaemonConfig` (config.py:77-85): `enabled`, schedules, `port=8787`, `approval_timeout_hours=8`, `approval_on_timeout="defer"`
- `WorkflowConfig` (config.py:42-64): `task_source`, `human_in_the_loop`, `default_branch="main"`, `pr_target_branch="main"`, `corporate_report_channels`, `approval_push_channels`, `todo_path`, `hitl_strategy="per_plan"`, `daemon`
- Existing `reporter_node` (reporter.py:21-104): calls `_placeholder_pr_url` (line 79, returns synthetic example.com URLs), `_publish_to_channels` (line 80), `_update_task_status` (line 83), `_record_todo_result` (line 87)
- Existing `GitWorktreeManager` (tools/git_worktree.py:20): `add_and_commit(message)`, `get_diff(target)`, `branch_name` property — NO push method exists
- `github`/`gitlab` currently in `_STUB_CHANNELS` in notifications/factory.py:37 (skipped with warning)
- `ReporterResponse` (schemas.py:80-86): `pr_title: str`, `pr_description: str`, `corporate_report: str`
- Env override pattern: `DEVFLOW_*` prefix

---

## File Structure

| File | Responsibility |
|---|---|
| `src/devflow/forge/base.py` | NEW: abstract `ForgeBackend` base class (push, create_mr, healthcheck) |
| `src/devflow/forge/github.py` | NEW: `GitHubBackend` — push via GitPython, MR via GitHub REST API |
| `src/devflow/forge/gitlab.py` | NEW: `GitLabBackend` — push via GitPython, MR via GitLab REST API |
| `src/devflow/forge/factory.py` | NEW: `build_forge_backend(workflow_cfg)` + `_FORGE_REGISTRY` + `register_forge_backend` |
| `src/devflow/config.py` | Modify: add `ForgeConfig` model, `WorkflowConfig.forge` field |
| `src/devflow/schemas.py` | Modify: `ReporterResponse` += `commit_message: str` |
| `src/devflow/state.py` | Modify: `WorkflowState` += `mr_url`, `pushed_sha` fields |
| `src/devflow/nodes/reporter.py` | Modify: split into `prepare_report` + `execute_actions`; replace `_placeholder_pr_url` with real forge; strategy-gated actions |
| `src/devflow/nodes/publish_approval.py` | Modify: pass `commit_message` in interrupt payload for full_detail review |
| `config/workflow.yaml` | Modify: add `forge:` section |
| `config/agents/reporter.md` | Modify: reference conventions-skill files |
| `config/conventions/mr.md` | NEW: MR title/body format conventions (instruction doc, not code) |
| `config/conventions/commit.md` | NEW: commit message format conventions |
| `.env.example` | Modify: document `GITHUB_TOKEN`, `GITLAB_TOKEN`, `FORGE_*` |
| `tests/unit/forge/__init__.py` | NEW |
| `tests/unit/forge/test_base.py` | NEW |
| `tests/unit/forge/test_github.py` | NEW |
| `tests/unit/forge/test_gitlab.py` | NEW |
| `tests/unit/forge/test_factory.py` | NEW |
| `tests/unit/test_reporter.py` | Modify: test config-driven actions, forge integration |
| `tests/unit/test_config.py` | Modify: test `ForgeConfig` |

---

## Task 1: ForgeConfig and ReporterResponse.commit_message

**Files:**
- Modify: `src/devflow/config.py:42-64` (WorkflowConfig + new ForgeConfig)
- Modify: `src/devflow/schemas.py:80-86` (ReporterResponse)
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Consumes: existing `WorkflowConfig` (config.py:42)
- Produces: `ForgeConfig` model with `provider: str = "none"`, `target_branch: str = "main"`, `actions: list[str] = ["publish_report", "update_tracker", "record_todo"]`. `WorkflowConfig.forge: ForgeConfig` field. `ReporterResponse.commit_message: str`.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_config.py`:

```python
def test_workflow_config_has_forge_defaults() -> None:
    """WorkflowConfig gets sensible forge defaults when not specified."""
    from devflow.config import WorkflowConfig

    cfg = WorkflowConfig(task_source="mock")
    assert cfg.forge.provider == "none"
    assert cfg.forge.target_branch == "main"
    assert cfg.forge.actions == ["publish_report", "update_tracker", "record_todo"]


def test_forge_config_from_yaml(tmp_path: Path) -> None:
    """Forge config loads from YAML."""
    from devflow.config import load_workflow_config

    yaml_path = tmp_path / "workflow.yaml"
    yaml_path.write_text(
        "task_source: mock\n"
        "forge:\n"
        "  provider: github\n"
        "  target_branch: develop\n"
        "  actions:\n"
        "    - publish_report\n"
        "    - update_tracker\n"
        "    - record_todo\n"
        "    - push\n"
        "    - create_mr\n",
        encoding="utf-8",
    )
    cfg = load_workflow_config(yaml_path)
    assert cfg.forge.provider == "github"
    assert cfg.forge.target_branch == "develop"
    assert "push" in cfg.forge.actions
    assert "create_mr" in cfg.forge.actions
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_config.py::test_workflow_config_has_forge_defaults tests/unit/test_config.py::test_forge_config_from_yaml -v`
Expected: FAIL with `AttributeError: 'WorkflowConfig' object has no attribute 'forge'`

- [ ] **Step 3: Write minimal implementation**

In `src/devflow/config.py`, add the `ForgeConfig` class after `DaemonConfig` (after line 85):

```python
class ForgeConfig(BaseModel):
    """Configuration for the forge backend (GitHub/GitLab push + MR)."""

    provider: str = "none"  # none | github | gitlab | auto
    target_branch: str = "main"
    actions: list[str] = Field(
        default_factory=lambda: ["publish_report", "update_tracker", "record_todo"]
    )
```

Add the field to `WorkflowConfig` (after `daemon` field, line 64):

```python
    forge: ForgeConfig = Field(default_factory=lambda: ForgeConfig())
```

In `src/devflow/schemas.py`, add `commit_message` to `ReporterResponse` (after `corporate_report`, line 86):

```python
class ReporterResponse(BaseModel):
    """Response from the reporter agent."""

    pr_title: str
    pr_description: str
    corporate_report: str
    commit_message: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_config.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Run full config + reporter test suites to check no regressions**

Run: `python -m pytest tests/unit/test_config.py tests/unit/test_reporter.py -v`
Expected: All tests PASS (the new `commit_message` field has a default so existing reporter tests won't break).

- [ ] **Step 6: Commit**

```bash
git add src/devflow/config.py src/devflow/schemas.py tests/unit/test_config.py
git commit -m "feat(config): add ForgeConfig, ReporterResponse.commit_message

ForgeConfig: provider (none|github|gitlab|auto), target_branch, actions
list. ReporterResponse gains commit_message for convention-based commit
messages generated by the reporter LLM."
```

---

## Task 2: ForgeBackend abstract base class

**Files:**
- Create: `src/devflow/forge/__init__.py`
- Create: `src/devflow/forge/base.py`
- Test: `tests/unit/forge/__init__.py`, `tests/unit/forge/test_base.py`

**Interfaces:**
- Consumes: nothing (standalone abstract class)
- Produces: `ForgeBackend` ABC with `name: str`, `__init__(config: dict)`, `push(branch, target, repo_path) -> str`, `create_mr(branch, target, title, description) -> str`, `healthcheck() -> bool`, `close()`. The `MRInfo` Pydantic model: `url: str`, `number: int | None`.

- [ ] **Step 1: Create package inits**

Create `src/devflow/forge/__init__.py`:
```python
"""Forge backends: push branches and create merge requests."""
```

Create `tests/unit/forge/__init__.py`:
```python
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/forge/test_base.py`:

```python
"""Unit tests for the ForgeBackend abstract base class."""

from __future__ import annotations

import pytest

from devflow.forge.base import ForgeBackend, MRInfo


def test_mrinfo_model() -> None:
    """MRInfo holds url and optional number."""
    info = MRInfo(url="https://github.com/owner/repo/pull/42", number=42)
    assert info.url == "https://github.com/owner/repo/pull/42"
    assert info.number == 42

    info_no_num = MRInfo(url="https://gitlab.com/owner/repo/-/merge_requests/1")
    assert info_no_num.number is None


def test_forge_backend_is_abstract() -> None:
    """ForgeBackend cannot be instantiated directly."""
    with pytest.raises(TypeError):
        ForgeBackend({})  # type: ignore[abstract]


def test_forge_backend_default_healthcheck() -> None:
    """A concrete subclass gets the default healthcheck (returns True)."""

    class DummyBackend(ForgeBackend):
        name = "dummy"

        def push(self, branch: str, target: str, repo_path: str) -> str:
            return "sha-dummy"

        def create_mr(self, branch: str, target: str, title: str, description: str) -> MRInfo:
            return MRInfo(url="https://example.com/mr/1", number=1)

    backend = DummyBackend({})
    assert backend.healthcheck() is True
    # close() is a no-op by default
    backend.close()


def test_forge_backend_config_stored() -> None:
    """The config dict is accessible on the backend instance."""

    class DummyBackend(ForgeBackend):
        name = "dummy"

        def push(self, branch: str, target: str, repo_path: str) -> str:
            return "sha"

        def create_mr(self, branch: str, target: str, title: str, description: str) -> MRInfo:
            return MRInfo(url="https://example.com/mr/1")

    backend = DummyBackend({"token": "abc", "repo": "owner/repo"})
    assert backend.config["token"] == "abc"
    assert backend.config["repo"] == "owner/repo"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/unit/forge/test_base.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devflow.forge'`

- [ ] **Step 4: Write minimal implementation**

Create `src/devflow/forge/base.py`:

```python
"""Abstract base class for forge backends (GitHub, GitLab, etc.).

Mirrors the plug-in pattern used by :class:`devflow.mcp.base.TaskSource`
and :class:`devflow.notifications.base.NotificationChannel`: a config dict,
abstract ``push`` and ``create_mr``, optional ``healthcheck``/``close``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class MRInfo(BaseModel):
    """Result of creating a merge request / pull request."""

    url: str
    number: int | None = None


class ForgeBackend(ABC):
    """Adapter that pushes branches and creates merge requests on a forge.

    ``push`` pushes a local branch to the remote. ``create_mr`` opens a
    merge request / pull request. Both return identifying info.
    """

    name: str = "base"

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    @abstractmethod
    def push(self, branch: str, target: str, repo_path: str) -> str:
        """Push ``branch`` to the remote. Returns the pushed commit SHA."""

    @abstractmethod
    def create_mr(
        self, branch: str, target: str, title: str, description: str
    ) -> MRInfo:
        """Create a merge request from ``branch`` into ``target``.

        Returns an :class:`MRInfo` with the MR URL and optional number.
        If an MR already exists for this branch, return the existing one
        (idempotent).
        """

    def healthcheck(self) -> bool:
        """Return True when the backend is ready (token set, repo reachable)."""
        return True

    def close(self) -> None:  # noqa: B027 - optional hook
        """Release any resources (HTTP clients)."""
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/forge/test_base.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/devflow/forge/__init__.py src/devflow/forge/base.py tests/unit/forge/__init__.py tests/unit/forge/test_base.py
git commit -m "feat(forge): add ForgeBackend abstract base class

ForgeBackend ABC with push(branch, target, repo_path) -> sha and
create_mr(branch, target, title, description) -> MRInfo. Mirrors the
TaskSource/NotificationChannel plug-in pattern."
```

---

## Task 3: GitHubBackend implementation

**Files:**
- Create: `src/devflow/forge/github.py`
- Test: `tests/unit/forge/test_github.py`

**Interfaces:**
- Consumes: `ForgeBackend` (Task 2), `MRInfo` (Task 2), `httpx` (web extra), `git.Repo` (GitPython)
- Produces: `GitHubBackend(ForgeBackend)` with `name = "github"`. Config keys: `token` (required, from `GITHUB_TOKEN`), `repo` (e.g. `"owner/repo"`, parsed from git remote if not set), `api_url` (default `https://api.github.com`). `push()` uses `Repo(repo_path).remotes.origin.push(branch)`. `create_mr()` POSTs to `/repos/{owner}/{repo}/pulls`, checks for existing PR first (GET `/pulls?head={branch}`).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/forge/test_github.py`:

```python
"""Unit tests for the GitHubBackend."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from devflow.forge.github import GitHubBackend
from devflow.forge.base import MRInfo


def test_github_backend_name() -> None:
    """The backend name is 'github'."""
    backend = GitHubBackend({"token": "abc", "repo": "owner/repo"})
    assert backend.name == "github"


def test_github_push_uses_gitpython() -> None:
    """push() pushes the branch via GitPython."""
    backend = GitHubBackend({"token": "abc", "repo": "owner/repo"})

    with patch("devflow.forge.github.Repo") as mock_repo_cls:
        mock_repo = MagicMock()
        mock_remote = MagicMock()
        mock_remote.push.return_value = [MagicMock(new_rev_sha="abc123")]
        mock_repo.remotes.origin = mock_remote
        mock_repo_cls.return_value = mock_repo

        sha = backend.push("feature-branch", "main", "/path/to/repo")

    mock_remote.push.assert_called_once()
    assert sha is not None


def test_github_create_mr_posts_to_api() -> None:
    """create_mr() POSTs to the GitHub PR API and returns MRInfo."""
    backend = GitHubBackend({"token": "abc", "repo": "owner/repo"})

    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "html_url": "https://github.com/owner/repo/pull/42",
        "number": 42,
    }

    mock_list_response = MagicMock()
    mock_list_response.status_code = 200
    mock_list_response.raise_for_status = MagicMock()
    mock_list_response.json.return_value = []  # no existing PRs

    with patch("devflow.forge.github.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.get.return_value = mock_list_response
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mr_info = backend.create_mr(
            "feature-branch", "main", "Fix bug", "This fixes the bug"
        )

    assert isinstance(mr_info, MRInfo)
    assert mr_info.url == "https://github.com/owner/repo/pull/42"
    assert mr_info.number == 42
    # Verify the POST body
    post_call = mock_client.post.call_args
    assert "/repos/owner/repo/pulls" in post_call[0][0]
    body = post_call[1]["json"]
    assert body["title"] == "Fix bug"
    assert body["head"] == "feature-branch"
    assert body["base"] == "main"


def test_github_create_mr_returns_existing_if_present() -> None:
    """create_mr() returns an existing PR if one already exists for the branch."""
    backend = GitHubBackend({"token": "abc", "repo": "owner/repo"})

    mock_list_response = MagicMock()
    mock_list_response.status_code = 200
    mock_list_response.raise_for_status = MagicMock()
    mock_list_response.json.return_value = [
        {
            "html_url": "https://github.com/owner/repo/pull/99",
            "number": 99,
        }
    ]

    with patch("devflow.forge.github.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.get.return_value = mock_list_response
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mr_info = backend.create_mr("feature-branch", "main", "Fix", "desc")

    assert mr_info.number == 99
    assert mr_info.url == "https://github.com/owner/repo/pull/99"
    # POST should NOT have been called (existing PR found)
    mock_client.post.assert_not_called()


def test_github_healthcheck_without_token_returns_false() -> None:
    """healthcheck() returns False when token is missing."""
    backend = GitHubBackend({"repo": "owner/repo"})
    assert backend.healthcheck() is False


def test_github_healthcheck_with_token_and_repo_returns_true() -> None:
    """healthcheck() returns True when token and repo are set."""
    backend = GitHubBackend({"token": "abc", "repo": "owner/repo"})
    assert backend.healthcheck() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/forge/test_github.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devflow.forge.github'`

- [ ] **Step 3: Write minimal implementation**

Create `src/devflow/forge/github.py`:

```python
"""GitHub forge backend: push branches and create pull requests.

Uses GitPython for push (``repo.remotes.origin.push``) and the GitHub REST
API (via httpx) for pull request creation.

Environment variables (read if not in config dict):
    GITHUB_TOKEN  — personal access token
    GITHUB_REPO   — repository in ``owner/repo`` format
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from git import Repo

from devflow.forge.base import ForgeBackend, MRInfo

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"


class GitHubBackend(ForgeBackend):
    """Push branches and create PRs on GitHub."""

    name = "github"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._token = config.get("token") or os.getenv("GITHUB_TOKEN", "")
        self._repo = config.get("repo") or os.getenv("GITHUB_REPO", "")
        self._api_url = config.get("api_url") or os.getenv("GITHUB_API_URL", _GITHUB_API)

    def push(self, branch: str, target: str, repo_path: str) -> str:
        """Push ``branch`` to the ``origin`` remote. Returns the pushed SHA."""
        repo = Repo(repo_path)
        remote = repo.remotes.origin
        push_results = remote.push(refspec=f"HEAD:refs/heads/{branch}")
        if push_results:
            result = push_results[0]
            logger.info(
                "GitHub push: branch=%s, flags=%s, summary=%s",
                branch,
                result.flags,
                result.summary,
            )
        # Return the current HEAD SHA as the pushed commit identifier
        return str(repo.head.commit.hexsha)

    def create_mr(
        self, branch: str, target: str, title: str, description: str
    ) -> MRInfo:
        """Create a GitHub pull request (or return existing one).

        Idempotent: if a PR already exists for ``branch``, returns it.
        """
        if not self._token:
            raise ValueError("GitHubBackend requires a token (set GITHUB_TOKEN)")
        if not self._repo:
            raise ValueError("GitHubBackend requires a repo (set GITHUB_REPO to 'owner/repo')")

        headers = {
            "Authorization": f"token {self._token}",
            "Accept": "application/vnd.github+json",
        }
        base_url = f"{self._api_url}/repos/{self._repo}"

        with httpx.Client(timeout=30.0) as client:
            # Check for an existing PR (idempotency).
            list_resp = client.get(
                f"{base_url}/pulls",
                params={"head": f"{self._repo.split('/')[0]}:{branch}", "state": "open"},
                headers=headers,
            )
            list_resp.raise_for_status()
            existing = list_resp.json()
            if existing:
                pr = existing[0]
                logger.info("GitHub: existing PR #%s found for branch %s", pr["number"], branch)
                return MRInfo(url=pr["html_url"], number=pr["number"])

            # Create a new PR.
            body = {
                "title": title,
                "head": branch,
                "base": target,
                "body": description,
            }
            create_resp = client.post(f"{base_url}/pulls", json=body, headers=headers)
            create_resp.raise_for_status()
            pr_data = create_resp.json()

        logger.info("GitHub: created PR #%s for branch %s", pr_data["number"], branch)
        return MRInfo(url=pr_data["html_url"], number=pr_data["number"])

    def healthcheck(self) -> bool:
        """Return True when token and repo are configured."""
        return bool(self._token and self._repo)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/forge/test_github.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devflow/forge/github.py tests/unit/forge/test_github.py
git commit -m "feat(forge): add GitHubBackend (push + create PR)

push() via GitPython remote.push. create_mr() via GitHub REST API with
idempotency check (GET /pulls?head=branch first). Reads GITHUB_TOKEN,
GITHUB_REPO from env."
```

---

## Task 4: GitLabBackend implementation

**Files:**
- Create: `src/devflow/forge/gitlab.py`
- Test: `tests/unit/forge/test_gitlab.py`

**Interfaces:**
- Consumes: `ForgeBackend` (Task 2), `MRInfo` (Task 2), `httpx`, `git.Repo`
- Produces: `GitLabBackend(ForgeBackend)` with `name = "gitlab"`. Config keys: `token` (from `GITLAB_TOKEN`), `project_id` (int, parsed from git remote URL if not set), `api_url` (default `https://gitlab.com/api/v4`). `push()` same as GitHub. `create_mr()` POSTs to `/projects/{id}/merge_requests`, checks existing first.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/forge/test_gitlab.py`:

```python
"""Unit tests for the GitLabBackend."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from devflow.forge.gitlab import GitLabBackend
from devflow.forge.base import MRInfo


def test_gitlab_backend_name() -> None:
    """The backend name is 'gitlab'."""
    backend = GitLabBackend({"token": "abc", "project_id": 123})
    assert backend.name == "gitlab"


def test_gitlab_push_uses_gitpython() -> None:
    """push() pushes the branch via GitPython."""
    backend = GitLabBackend({"token": "abc", "project_id": 123})

    with patch("devflow.forge.gitlab.Repo") as mock_repo_cls:
        mock_repo = MagicMock()
        mock_remote = MagicMock()
        mock_remote.push.return_value = [MagicMock()]
        mock_repo.remotes.origin = mock_remote
        mock_repo.head.commit.hexsha = "def456"
        mock_repo_cls.return_value = mock_repo

        sha = backend.push("feature-branch", "main", "/path/to/repo")

    mock_remote.push.assert_called_once()
    assert sha == "def456"


def test_gitlab_create_mr_posts_to_api() -> None:
    """create_mr() POSTs to the GitLab MR API and returns MRInfo."""
    backend = GitLabBackend({"token": "abc", "project_id": 123})

    mock_list_response = MagicMock()
    mock_list_response.status_code = 200
    mock_list_response.raise_for_status = MagicMock()
    mock_list_response.json.return_value = []  # no existing MRs

    mock_create_response = MagicMock()
    mock_create_response.status_code = 201
    mock_create_response.raise_for_status = MagicMock()
    mock_create_response.json.return_value = {
        "web_url": "https://gitlab.com/owner/repo/-/merge_requests/42",
        "iid": 42,
    }

    with patch("devflow.forge.gitlab.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.get.return_value = mock_list_response
        mock_client.post.return_value = mock_create_response
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mr_info = backend.create_mr("feature-branch", "main", "Fix bug", "Description")

    assert isinstance(mr_info, MRInfo)
    assert mr_info.url == "https://gitlab.com/owner/repo/-/merge_requests/42"
    assert mr_info.number == 42
    post_call = mock_client.post.call_args
    assert "/projects/123/merge_requests" in post_call[0][0]
    body = post_call[1]["json"]
    assert body["title"] == "Fix bug"
    assert body["source_branch"] == "feature-branch"
    assert body["target_branch"] == "main"


def test_gitlab_create_mr_returns_existing_if_present() -> None:
    """create_mr() returns an existing MR if one already exists."""
    backend = GitLabBackend({"token": "abc", "project_id": 123})

    mock_list_response = MagicMock()
    mock_list_response.status_code = 200
    mock_list_response.raise_for_status = MagicMock()
    mock_list_response.json.return_value = [
        {
            "web_url": "https://gitlab.com/owner/repo/-/merge_requests/99",
            "iid": 99,
        }
    ]

    with patch("devflow.forge.gitlab.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.get.return_value = mock_list_response
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mr_info = backend.create_mr("feature-branch", "main", "Fix", "desc")

    assert mr_info.number == 99
    mock_client.post.assert_not_called()


def test_gitlab_healthcheck() -> None:
    """healthcheck() returns True when token and project_id are set."""
    assert GitLabBackend({"token": "abc", "project_id": 123}).healthcheck() is True
    assert GitLabBackend({}).healthcheck() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/forge/test_gitlab.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devflow.forge.gitlab'`

- [ ] **Step 3: Write minimal implementation**

Create `src/devflow/forge/gitlab.py`:

```python
"""GitLab forge backend: push branches and create merge requests.

Uses GitPython for push and the GitLab REST API (v4) for MR creation.

Environment variables (read if not in config dict):
    GITLAB_TOKEN      — personal access token
    GITLAB_PROJECT_ID — numeric project ID
    GITLAB_API_URL    — API base URL (default: https://gitlab.com/api/v4)
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from git import Repo

from devflow.forge.base import ForgeBackend, MRInfo

logger = logging.getLogger(__name__)

_GITLAB_API = "https://gitlab.com/api/v4"


class GitLabBackend(ForgeBackend):
    """Push branches and create MRs on GitLab."""

    name = "gitlab"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._token = config.get("token") or os.getenv("GITLAB_TOKEN", "")
        project_id = config.get("project_id") or os.getenv("GITLAB_PROJECT_ID", "")
        self._project_id = str(project_id) if project_id else ""
        self._api_url = config.get("api_url") or os.getenv("GITLAB_API_URL", _GITLAB_API)

    def push(self, branch: str, target: str, repo_path: str) -> str:
        """Push ``branch`` to the ``origin`` remote. Returns the pushed SHA."""
        repo = Repo(repo_path)
        remote = repo.remotes.origin
        push_results = remote.push(refspec=f"HEAD:refs/heads/{branch}")
        if push_results:
            result = push_results[0]
            logger.info(
                "GitLab push: branch=%s, flags=%s, summary=%s",
                branch,
                result.flags,
                result.summary,
            )
        return str(repo.head.commit.hexsha)

    def create_mr(
        self, branch: str, target: str, title: str, description: str
    ) -> MRInfo:
        """Create a GitLab merge request (or return existing one).

        Idempotent: if an MR already exists for ``branch``, returns it.
        """
        if not self._token:
            raise ValueError("GitLabBackend requires a token (set GITLAB_TOKEN)")
        if not self._project_id:
            raise ValueError("GitLabBackend requires a project_id (set GITLAB_PROJECT_ID)")

        headers = {"PRIVATE-TOKEN": self._token}
        base_url = f"{self._api_url}/projects/{self._project_id}/merge_requests"

        with httpx.Client(timeout=30.0) as client:
            # Check for an existing MR (idempotency).
            list_resp = client.get(
                base_url,
                params={"source_branch": branch, "state": "opened"},
                headers=headers,
            )
            list_resp.raise_for_status()
            existing = list_resp.json()
            if existing:
                mr = existing[0]
                logger.info("GitLab: existing MR !%s found for branch %s", mr["iid"], branch)
                return MRInfo(url=mr["web_url"], number=mr["iid"])

            # Create a new MR.
            body = {
                "title": title,
                "source_branch": branch,
                "target_branch": target,
                "description": description,
            }
            create_resp = client.post(base_url, json=body, headers=headers)
            create_resp.raise_for_status()
            mr_data = create_resp.json()

        logger.info("GitLab: created MR !%s for branch %s", mr_data["iid"], branch)
        return MRInfo(url=mr_data["web_url"], number=mr_data["iid"])

    def healthcheck(self) -> bool:
        """Return True when token and project_id are configured."""
        return bool(self._token and self._project_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/forge/test_gitlab.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devflow/forge/gitlab.py tests/unit/forge/test_gitlab.py
git commit -m "feat(forge): add GitLabBackend (push + create MR)

push() via GitPython remote.push. create_mr() via GitLab REST API v4
with idempotency check (GET /merge_requests?source_branch= first).
Reads GITLAB_TOKEN, GITLAB_PROJECT_ID from env."
```

---

## Task 5: Forge factory

**Files:**
- Create: `src/devflow/forge/factory.py`
- Test: `tests/unit/forge/test_factory.py`

**Interfaces:**
- Consumes: `ForgeConfig` (Task 1), `GitHubBackend` (Task 3), `GitLabBackend` (Task 4), `WorkflowConfig`
- Produces: `build_forge_backend(workflow_cfg) -> ForgeBackend | None` — returns `None` when `provider == "none"`. `register_forge_backend(name, cls)` hook. When `provider == "auto"`, parses `git remote get-url origin` to determine github/gitlab.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/forge/test_factory.py`:

```python
"""Unit tests for the forge factory."""

from __future__ import annotations

import pytest

from devflow.config import ForgeConfig, WorkflowConfig
from devflow.forge.base import ForgeBackend
from devflow.forge.factory import build_forge_backend


def _make_workflow(provider: str = "none") -> WorkflowConfig:
    wf = WorkflowConfig(task_source="mock")
    wf.forge = ForgeConfig(provider=provider)
    return wf


def test_build_forge_returns_none_for_none_provider() -> None:
    """build_forge_backend returns None when provider is 'none'."""
    wf = _make_workflow("none")
    assert build_forge_backend(wf) is None


def test_build_forge_github() -> None:
    """build_forge_backend returns GitHubBackend when provider is 'github'."""
    wf = _make_workflow("github")
    backend = build_forge_backend(wf)
    assert backend is not None
    assert backend.name == "github"


def test_build_forge_gitlab() -> None:
    """build_forge_backend returns GitLabBackend when provider is 'gitlab'."""
    wf = _make_workflow("gitlab")
    backend = build_forge_backend(wf)
    assert backend is not None
    assert backend.name == "gitlab"


def test_build_forge_unknown_provider_raises() -> None:
    """build_forge_backend raises ValueError for an unknown provider."""
    wf = _make_workflow("bogus")
    with pytest.raises(ValueError, match="Unknown forge provider"):
        build_forge_backend(wf)


def test_register_custom_forge_backend() -> None:
    """register_forge_backend adds a new provider to the registry."""
    from devflow.forge.base import MRInfo
    from devflow.forge.factory import register_forge_backend

    class CustomBackend(ForgeBackend):
        name = "custom"

        def push(self, branch: str, target: str, repo_path: str) -> str:
            return "sha"

        def create_mr(self, branch: str, target: str, title: str, description: str) -> MRInfo:
            return MRInfo(url="https://custom/mr/1")

    register_forge_backend("custom", CustomBackend)
    wf = _make_workflow("custom")
    backend = build_forge_backend(wf)
    assert backend is not None
    assert backend.name == "custom"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/forge/test_factory.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devflow.forge.factory'`

- [ ] **Step 3: Write minimal implementation**

Create `src/devflow/forge/factory.py`:

```python
"""Factory for building ForgeBackend adapters from workflow configuration.

Mirrors :mod:`devflow.mcp.factory` and :mod:`devflow.notifications.factory`:
a registry mapping provider names to ``ForgeBackend`` subclasses, a
``build_forge_backend`` builder, and a ``register_forge_backend`` hook.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from devflow.config import WorkflowConfig
from devflow.forge.base import ForgeBackend
from devflow.forge.github import GitHubBackend
from devflow.forge.gitlab import GitLabBackend

logger = logging.getLogger(__name__)

_FORGE_REGISTRY: dict[str, type[ForgeBackend]] = {
    "github": GitHubBackend,
    "gitlab": GitLabBackend,
}


def build_forge_backend(workflow_cfg: WorkflowConfig) -> ForgeBackend | None:
    """Build a ForgeBackend from the workflow config.

    Returns ``None`` when ``forge.provider == "none"`` (no forge integration).
    When ``provider == "auto"``, parses ``git remote get-url origin`` to
    determine github or gitlab from the remote URL host.
    """
    provider = workflow_cfg.forge.provider

    if provider == "none":
        return None

    if provider == "auto":
        provider = _detect_provider_from_remote()
        if provider == "none":
            logger.warning("forge.provider=auto but could not detect provider from remote")
            return None

    cls = _FORGE_REGISTRY.get(provider)
    if cls is None:
        raise ValueError(
            f"Unknown forge provider '{provider}'. "
            f"Supported: {sorted(_FORGE_REGISTRY)} (+ 'none', 'auto')"
        )

    config = _build_config(provider)
    backend = cls(config)
    logger.info("Built forge backend: %s", backend.name)
    return backend


def _build_config(provider: str) -> dict[str, Any]:
    """Build the config dict for a forge backend from env vars."""
    if provider == "github":
        return {
            "token": os.getenv("GITHUB_TOKEN", ""),
            "repo": os.getenv("GITHUB_REPO", ""),
            "api_url": os.getenv("GITHUB_API_URL", "https://api.github.com"),
        }
    if provider == "gitlab":
        return {
            "token": os.getenv("GITLAB_TOKEN", ""),
            "project_id": os.getenv("GITLAB_PROJECT_ID", ""),
            "api_url": os.getenv("GITLAB_API_URL", "https://gitlab.com/api/v4"),
        }
    return {}


def _detect_provider_from_remote() -> str:
    """Detect forge provider from the git remote origin URL.

    Returns 'github', 'gitlab', or 'none'.
    """
    try:
        from git import Repo

        repo = Repo(os.getcwd())
        remote_url = repo.remotes.origin.url
    except Exception:
        return "none"

    remote_url = remote_url.lower()
    if "github.com" in remote_url:
        return "github"
    if "gitlab.com" in remote_url or "gitlab" in remote_url:
        return "gitlab"
    return "none"


def register_forge_backend(name: str, cls: type[ForgeBackend]) -> None:
    """Register a custom forge backend adapter."""
    if not issubclass(cls, ForgeBackend):
        raise TypeError(f"{cls} must be a subclass of ForgeBackend")
    _FORGE_REGISTRY[name] = cls
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/forge/test_factory.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devflow/forge/factory.py tests/unit/forge/test_factory.py
git commit -m "feat(forge): add forge factory with build_forge_backend

Registry: github + gitlab. provider='none' returns None. provider='auto'
detects from git remote URL. register_forge_backend hook for custom
backends. Mirrors mcp/notifications factory pattern."
```

---

## Task 6: Refactor reporter — split prepare_report + execute_actions

**Files:**
- Modify: `src/devflow/nodes/reporter.py:21-104` (split into prepare + execute)
- Modify: `src/devflow/state.py:130-162` (add `mr_url`, `pushed_sha`)
- Test: `tests/unit/test_reporter.py` (modify)

**Interfaces:**
- Consumes: `ForgeBackend` (Task 2), `build_forge_backend` (Task 5), `ForgeConfig.actions` (Task 1), existing `_publish_to_channels`, `_update_task_status`, `_record_todo_result`
- Produces: `reporter_node` now calls `prepare_report` (LLM generates artifacts) then `execute_actions` (deterministic, config-driven). `execute_actions` checks `forge_cfg.actions` list and only runs enabled actions. `_placeholder_pr_url` replaced by real `forge.create_mr` when `create_mr` in actions. `WorkflowState` gains `mr_url: str | None` and `pushed_sha: str | None`.

- [ ] **Step 1: Add new state fields**

In `src/devflow/state.py`, add to `WorkflowState` (after `report_url`, around line 152):

```python
    mr_url: str | None
    pushed_sha: str | None
```

- [ ] **Step 2: Write the failing test**

Add to `tests/unit/test_reporter.py`:

```python
def test_reporter_executes_only_enabled_actions(
    base_state: WorkflowState,
    mock_config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When forge.actions excludes create_mr, no MR is created."""
    mock_config.workflow.forge.actions = ["publish_report", "update_tracker", "record_todo"]

    push_called: list[bool] = []
    mr_called: list[bool] = []

    class FakeForge:
        name = "fake"

        def push(self, branch, target, repo_path):
            push_called.append(True)
            return "sha-fake"

        def create_mr(self, branch, target, title, description):
            mr_called.append(True)
            from devflow.forge.base import MRInfo
            return MRInfo(url="https://fake/mr/1", number=1)

        def healthcheck(self):
            return True

        def close(self):
            pass

    monkeypatch.setattr(
        "devflow.nodes.reporter.build_forge_backend", lambda wf: FakeForge()
    )

    result = reporter_node(base_state, app_cfg=mock_config)

    # push and create_mr were NOT called (not in actions list)
    assert push_called == []
    assert mr_called == []
    assert result.get("mr_url") is None


def test_reporter_creates_mr_when_action_enabled(
    base_state: WorkflowState,
    mock_config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When create_mr is in actions, the reporter creates an MR via forge."""
    mock_config.workflow.forge.actions = ["create_mr"]
    mock_config.workflow.forge.provider = "github"

    class FakeForge:
        name = "fake"

        def push(self, branch, target, repo_path):
            return "sha-fake"

        def create_mr(self, branch, target, title, description):
            from devflow.forge.base import MRInfo
            return MRInfo(url="https://github.com/owner/repo/pull/1", number=1)

        def healthcheck(self):
            return True

        def close(self):
            pass

    monkeypatch.setattr(
        "devflow.nodes.reporter.build_forge_backend", lambda wf: FakeForge()
    )

    result = reporter_node(base_state, app_cfg=mock_config)

    assert result.get("mr_url") == "https://github.com/owner/repo/pull/1"
    assert result.get("pr_url") is not None or result.get("mr_url") is not None


def test_reporter_pushes_when_action_enabled(
    base_state: WorkflowState,
    mock_config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When push is in actions, the reporter pushes the branch."""
    mock_config.workflow.forge.actions = ["push"]
    mock_config.workflow.forge.provider = "github"

    pushed: list[str] = []

    class FakeForge:
        name = "fake"

        def push(self, branch, target, repo_path):
            pushed.append(branch)
            return "sha-pushed"

        def create_mr(self, branch, target, title, description):
            from devflow.forge.base import MRInfo
            return MRInfo(url="https://fake/mr/1", number=1)

        def healthcheck(self):
            return True

        def close(self):
            pass

    monkeypatch.setattr(
        "devflow.nodes.reporter.build_forge_backend", lambda wf: FakeForge()
    )

    result = reporter_node(base_state, app_cfg=mock_config)

    assert len(pushed) == 1
    assert result.get("pushed_sha") == "sha-pushed"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_reporter.py::test_reporter_executes_only_enabled_actions -v`
Expected: FAIL — reporter currently doesn't check `forge.actions`, doesn't have `build_forge_backend` import.

- [ ] **Step 4: Refactor reporter_node**

In `src/devflow/nodes/reporter.py`, add import at the top (after existing imports):

```python
from devflow.forge.factory import build_forge_backend
```

Replace the `_placeholder_pr_url` call and action execution block (lines 79-87) in `reporter_node`. The new `reporter_node` main body (after the `call_structured` LLM call at line 62 and `_build_report_markdown` at line 65) should look like:

```python
        # Build the report markdown for notification channels.
        report_text = _build_report_markdown(
            task=task,
            response=response,
            verdict=verdict,
            reports=reports,
            branch=branch,
        )

        # Surface errors via notification channels (best-effort).
        error = state.get("error")
        if error is not None:
            _publish_to_channels(app_cfg, _build_error_markdown(task, error))

        # Execute config-driven actions.
        action_results = _execute_actions(
            app_cfg=app_cfg,
            state=state,
            task=task,
            response=response,
            verdict=verdict,
            report_text=report_text,
            branch=branch,
        )

        logger.info("Reporter finished for task %s", task.id)
        return {
            "pr_url": action_results.get("pr_url"),
            "mr_url": action_results.get("mr_url"),
            "pushed_sha": action_results.get("pushed_sha"),
            "report_url": action_results.get("report_url"),
            "task": action_results.get("task", task),
            "logs": [
                f"reporter: PR '{response.pr_title}'",
                "reporter: corporate report generated",
            ]
            + action_results.get("action_logs", []),
        }
```

Add the `_execute_actions` function (replaces `_placeholder_pr_url` logic). Place it after `_publish_to_channels` and before `_record_todo_result`:

```python
def _execute_actions(
    *,
    app_cfg: Config,
    state: WorkflowState,
    task: Task,
    response: ReporterResponse,
    verdict: FinalVerdict | None,
    report_text: str,
    branch: str | None,
) -> dict[str, Any]:
    """Execute config-driven publication actions.

    Each action is independent: a failure in one does not abort the others
    (same pattern as _publish_to_channels). Returns a dict of results.
    """
    forge_cfg = app_cfg.workflow.forge
    actions = forge_cfg.actions
    results: dict[str, Any] = {"action_logs": []}
    forge: Any = None

    try:
        forge = build_forge_backend(app_cfg.workflow)
    except Exception as exc:
        logger.warning("Failed to build forge backend: %s", exc)

    # publish_report: send the corporate report to notification channels.
    if "publish_report" in actions:
        try:
            report_url = _publish_to_channels(app_cfg, report_text)
            results["report_url"] = report_url
        except Exception as exc:
            logger.warning("publish_report action failed: %s", exc)

    # update_tracker: update task status in the external tracker.
    if "update_tracker" in actions:
        try:
            updated_task = _update_task_status(app_cfg, task, verdict)
            results["task"] = updated_task
        except Exception as exc:
            logger.warning("update_tracker action failed: %s", exc)

    # record_todo: write result back into TODO.md.
    if "record_todo" in actions:
        try:
            _record_todo_result(app_cfg, state, response, verdict)
        except Exception as exc:
            logger.warning("record_todo action failed: %s", exc)

    # push: push the branch to the remote via forge backend.
    if "push" in actions and forge is not None and branch:
        try:
            repo_path = state.get("worktree_path") or "."
            sha = forge.push(branch, forge_cfg.target_branch, repo_path)
            results["pushed_sha"] = sha
            results["action_logs"].append(f"reporter: pushed {branch} -> {sha[:8]}")
        except Exception as exc:
            logger.warning("push action failed: %s", exc)

    # create_mr: create a merge request via forge backend.
    if "create_mr" in actions and forge is not None and branch:
        try:
            mr_info = forge.create_mr(
                branch=branch,
                target=forge_cfg.target_branch,
                title=response.pr_title,
                description=response.pr_description,
            )
            results["mr_url"] = mr_info.url
            results["pr_url"] = mr_info.url  # backward compat
            results["action_logs"].append(f"reporter: MR created {mr_info.url}")
        except Exception as exc:
            logger.warning("create_mr action failed: %s", exc)

    # Close the forge backend if it was opened.
    if forge is not None:
        try:
            forge.close()
        except Exception:  # pragma: no cover
            pass

    return results
```

Remove the `_placeholder_pr_url` function entirely (it's replaced by real forge).

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_reporter.py -v`
Expected: All tests PASS (new + existing). The existing tests that checked `pr_url` should still pass because `create_mr` is not in the default `actions` list, so `pr_url` will be `None` — update assertions if needed.

**IMPORTANT:** The existing `test_reporter_publishes_to_console` asserts `result["report_url"] == "console"` — this should still pass because `publish_report` is in the default actions. The existing `base_state` fixture uses `mock_config` which has `forge.actions = ["publish_report", "update_tracker", "record_todo"]` by default.

- [ ] **Step 6: Run full test suite**

Run: `python -m pytest tests/ -v --ignore=tests/integration`
Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/devflow/nodes/reporter.py src/devflow/state.py tests/unit/test_reporter.py
git commit -m "feat(reporter): split into prepare_report + execute_actions

reporter_node now generates artifacts (LLM) then executes config-driven
actions (publish_report, update_tracker, record_todo, push, create_mr).
_placeholder_pr_url replaced by real forge.create_mr. WorkflowState gains
mr_url and pushed_sha fields."
```

---

## Task 7: Conventions-skill markdown files + reporter agent prompt

**Files:**
- Create: `config/conventions/mr.md`
- Create: `config/conventions/commit.md`
- Modify: `config/agents/reporter.md`

**Interfaces:**
- Consumes: nothing (markdown instruction files for the LLM)
- Produces: convention docs referenced by the reporter agent prompt

- [ ] **Step 1: Create mr.md conventions**

Create `config/conventions/mr.md`:

```markdown
# Merge Request Conventions

## Title format
Use the pattern: `<type>(<scope>): <subject>`

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`
Scope: the module or component affected (optional)
Subject: imperative mood, lowercase, no period

Examples:
- `feat(auth): add session timeout`
- `fix(api): handle null response in user endpoint`
- `refactor(db): extract connection pool`

## Description format
```
## What
<one-sentence summary of the change>

## Why
<the problem or motivation>

## How
<bullet points of the key implementation decisions>

## Testing
<how the change was tested>
```

## Checklist
- [ ] Title follows the format above
- [ ] Description has What/Why/How/Testing sections
- [ ] No sensitive data (tokens, keys) in the description
```

- [ ] **Step 2: Create commit.md conventions**

Create `config/conventions/commit.md`:

```markdown
# Commit Message Conventions

Follow the Conventional Commits specification:

```
<type>(<scope>): <subject>

<body>
```

## Types
- `feat`: a new feature
- `fix`: a bug fix
- `refactor`: code change that neither fixes a bug nor adds a feature
- `docs`: documentation only changes
- `test`: adding or correcting tests
- `chore`: build process, auxiliary tools, dependencies
- `perf`: code change that improves performance

## Rules
- Subject line: imperative mood ("add" not "added"), lowercase, no period, max 72 chars
- Body: explain *what* and *why* (not *how*), wrap at 72 chars
- Reference the task/issue ID when applicable

## Examples
```
feat(auth): add session timeout redirect

Redirect users to /login when the session expires instead of showing
a blank page. Refs #4321.
```
```

- [ ] **Step 3: Update reporter agent prompt**

In `config/agents/reporter.md`, update the Instructions section to reference conventions:

Replace the entire file content with:

```markdown
---
name: reporter
provider: kimi
model: kimi-code/kimi-for-coding
temperature: 0.3
skills:
  - technical-writing
  - reporting
tools: []
---

# Role
You are a technical writer and release engineer producing workflow reports and merge request descriptions.

# Instructions
Summarize the task, plan, implementation, review verdicts, and any rework iterations. Produce a concise human-readable report suitable for console output or corporate notification channels. Be factual and highlight actionable items.

Additionally, generate:
- **pr_title** and **pr_description**: follow the conventions in `config/conventions/mr.md` for the title format and description structure.
- **commit_message**: follow the conventions in `config/conventions/commit.md` for the commit message format.
- **corporate_report**: a concise summary of the workflow outcome for stakeholder notification.

Respond in the language specified by the task or the corporate standard. If no language is specified, default to English.
```

- [ ] **Step 4: Commit**

```bash
git add config/conventions/mr.md config/conventions/commit.md config/agents/reporter.md
git commit -m "docs: add conventions-skill files (mr.md, commit.md) + update reporter

Convention markdown files let teams customize MR title/body and commit
message formats without code changes. Reporter agent prompt references
them. Language defaults to English unless overridden."
```

---

## Task 8: Update config files and .env.example

**Files:**
- Modify: `config/workflow.yaml`
- Modify: `.env.example`

**Interfaces:**
- Consumes: `ForgeConfig` (Task 1)

- [ ] **Step 1: Update config/workflow.yaml**

Add the `forge:` section to `config/workflow.yaml` (after `daemon:` section):

```yaml

# Forge backend configuration (push branches + create merge requests)
forge:
  provider: none    # none | github | gitlab | auto (auto = detect from git remote)
  target_branch: main
  actions:
    - publish_report
    - update_tracker
    - record_todo
    # Enable push and create_mr for per_plan/full_detail modes, or leave
    # them disabled here and they'll be auto-enabled in end_of_day batch:
    # - push
    # - create_mr
```

- [ ] **Step 2: Update .env.example**

Append to `.env.example`:

```env
# ── Forge backend (push + MR) ─────────────────────────────────────────────
# GitHub:
# GITHUB_TOKEN=ghp_xxx
# GITHUB_REPO=owner/repo
# GITHUB_API_URL=https://api.github.com   # override for GitHub Enterprise
#
# GitLab:
# GITLAB_TOKEN=glpat-xxx
# GITLAB_PROJECT_ID=123
# GITLAB_API_URL=https://gitlab.com/api/v4  # override for self-hosted
```

- [ ] **Step 3: Verify config loads**

Run: `python -c "from devflow.config import load_config; cfg = load_config('config'); print(cfg.workflow.forge)"`
Expected: Prints `provider='none' target_branch='main' actions=['publish_report', 'update_tracker', 'record_todo']`

- [ ] **Step 4: Commit**

```bash
git add config/workflow.yaml .env.example
git commit -m "docs: add forge section to workflow.yaml, document forge env vars

provider=none by default. push/create_mr commented out (enable per
strategy or for end_of_day batch). GITHUB_TOKEN/REPO and GITLAB_TOKEN/
PROJECT_ID documented in .env.example."
```

---

## Task 9: Integration test — forge push + MR through reporter

**Files:**
- Create: `tests/integration/test_forge_reporter.py`
- Test: itself

**Interfaces:**
- Consumes: all Phase 3 components

- [ ] **Step 1: Write integration test**

Create `tests/integration/test_forge_reporter.py`:

```python
"""Integration test: forge push + MR through the reporter node.

Verifies that when forge.actions includes push + create_mr, the reporter
calls the forge backend to push the branch and create an MR.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from git import Repo

from devflow.config import Config, HitlStrategy
from devflow.forge.base import MRInfo
from devflow.nodes.reporter import reporter_node
from devflow.schemas import Plan, PlanStep
from devflow.state import CheckerReport, CheckerVerdict, FinalVerdict, Task, WorkflowState


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    repo = Repo.init(repo_path)
    with repo.config_writer() as writer:
        writer.set_value("user", "name", "Test")
        writer.set_value("user", "email", "test@example.com")
    (repo_path / "README.md").write_text("# init", encoding="utf-8")
    repo.git.add("--all")
    repo.index.commit("init")
    if "main" not in repo.heads:
        repo.create_head("main")
    return repo_path


def _make_state(repo_path: str) -> WorkflowState:
    task = Task(id="T-1", title="Test task", description="A test task")
    plan = Plan(summary="Do thing", steps=[PlanStep(id="s1", description="step")])
    report = CheckerReport(
        agent_name="checker_a",
        verdict=CheckerVerdict.APPROVE,
        summary="ok",
    )
    return WorkflowState(
        task=task,
        plan=plan,
        diff="--- a\n+++ b\n+hello\n",
        checker_reports=[report],
        final_verdict=FinalVerdict.APPROVE,
        branch_name="devflow/T-1/abc12345",
        worktree_path=repo_path,
    )


class RecordingForge:
    """A fake forge backend that records push and create_mr calls."""

    name = "recording"

    def __init__(self) -> None:
        self.pushed: list[tuple[str, str, str]] = []
        self.mrs_created: list[dict[str, str]] = []

    def push(self, branch: str, target: str, repo_path: str) -> str:
        self.pushed.append((branch, target, repo_path))
        return "sha-recorded"

    def create_mr(self, branch: str, target: str, title: str, description: str) -> MRInfo:
        self.mrs_created.append({
            "branch": branch,
            "target": target,
            "title": title,
            "description": description,
        })
        return MRInfo(url="https://example.com/mr/1", number=1)

    def healthcheck(self) -> bool:
        return True

    def close(self) -> None:
        pass


def test_reporter_pushes_and_creates_mr(
    temp_git_repo: Path,
    mock_config: Config,
    fake_llm_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reporter pushes branch and creates MR when both actions are enabled."""
    state = _make_state(str(temp_git_repo))

    mock_config.workflow.forge.provider = "github"
    mock_config.workflow.forge.actions = ["push", "create_mr"]

    recording_forge = RecordingForge()
    monkeypatch.setattr(
        "devflow.nodes.reporter.build_forge_backend", lambda wf: recording_forge
    )

    result = reporter_node(state, app_cfg=mock_config)

    # push was called with the branch name
    assert len(recording_forge.pushed) == 1
    assert recording_forge.pushed[0][0] == "devflow/T-1/abc12345"

    # create_mr was called
    assert len(recording_forge.mrs_created) == 1
    assert recording_forge.mrs_created[0]["branch"] == "devflow/T-1/abc12345"

    # Results propagated to state
    assert result.get("pushed_sha") == "sha-recorded"
    assert result.get("mr_url") == "https://example.com/mr/1"


def test_reporter_skips_forge_when_actions_excluded(
    temp_git_repo: Path,
    mock_config: Config,
    fake_llm_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reporter does not push or create MR when actions are excluded."""
    state = _make_state(str(temp_git_repo))

    mock_config.workflow.forge.provider = "github"
    mock_config.workflow.forge.actions = ["publish_report"]  # no push, no create_mr

    recording_forge = RecordingForge()
    monkeypatch.setattr(
        "devflow.nodes.reporter.build_forge_backend", lambda wf: recording_forge
    )

    result = reporter_node(state, app_cfg=mock_config)

    assert recording_forge.pushed == []
    assert recording_forge.mrs_created == []
    assert result.get("pushed_sha") is None
    assert result.get("mr_url") is None
```

- [ ] **Step 2: Run the integration tests**

Run: `python -m pytest tests/integration/test_forge_reporter.py -v`
Expected: Both tests PASS.

- [ ] **Step 3: Run the FULL test suite**

Run: `python -m pytest tests/ -v`
Expected: ALL tests PASS.

- [ ] **Step 4: Run ruff and mypy**

Run: `python -m ruff check src/devflow/forge/ src/devflow/nodes/reporter.py tests/unit/forge/ tests/integration/test_forge_reporter.py`
Expected: No errors.

Run: `python -m mypy src/devflow/forge/ src/devflow/nodes/reporter.py`
Expected: No errors.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_forge_reporter.py
git commit -m "test(integration): forge push + MR through reporter

Verifies reporter pushes branch and creates MR when both actions are
enabled, and skips forge entirely when actions exclude push/create_mr."
```

---

## Self-Review

### Spec coverage (Phase 3 scope)

| Spec section | Task(s) | Covered? |
|---|---|---|
| ForgeBackend abstraction (push, create_mr) | Task 2 | ✅ |
| GitHubBackend (push via GitPython, MR via REST API) | Task 3 | ✅ |
| GitLabBackend (push via GitPython, MR via REST API) | Task 4 | ✅ |
| Forge factory (build_forge_backend, registry, auto-detect) | Task 5 | ✅ |
| Reporter refactor: prepare_report + execute_actions | Task 6 | ✅ |
| Config-driven actions (publish_report, update_tracker, push, create_mr) | Task 6 | ✅ |
| Idempotent create_mr (return existing) | Tasks 3, 4 | ✅ |
| ReporterResponse.commit_message | Task 1 | ✅ |
| Conventions-skill files (mr.md, commit.md) | Task 7 | ✅ |
| Reporter agent prompt references conventions | Task 7 | ✅ |
| ForgeConfig (provider, target_branch, actions) | Task 1 | ✅ |
| Config documentation (workflow.yaml, .env.example) | Task 8 | ✅ |
| Integration test (forge push + MR through reporter) | Task 9 | ✅ |
| WorkflowState gains mr_url, pushed_sha | Task 6 | ✅ |

**Phase 4-5 items (NOT in this plan, correctly deferred):**
- EOD batch-store / batch-publish → Phase 4
- Vue SPA / SSE endpoint → Phase 5

### Placeholder scan
Searched for TBD/TODO/FIXME/"implement later"/"similar to" — none found in actionable steps. All code blocks contain complete implementations.

### Type consistency
- `ForgeBackend.push(branch, target, repo_path) -> str` — consistent across Task 2 (definition), Task 3 (GitHub), Task 4 (GitLab), Task 6 (reporter), Task 9 (integration test).
- `ForgeBackend.create_mr(branch, target, title, description) -> MRInfo` — consistent across Tasks 2, 3, 4, 6, 9.
- `MRInfo(url: str, number: int | None = None)` — consistent across Tasks 2, 3, 4, 6, 9.
- `build_forge_backend(workflow_cfg) -> ForgeBackend | None` — consistent across Task 5 (definition), Task 6 (reporter), Task 9 (integration).
- `ForgeConfig(provider, target_branch, actions)` — consistent across Task 1 (definition), Task 5 (factory), Task 6 (reporter), Task 8 (config).
- `ReporterResponse.pr_title, pr_description, corporate_report, commit_message` — consistent across Task 1, Task 6, Task 7 (prompt).

No type/name mismatches found.
