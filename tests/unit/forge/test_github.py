"""Unit tests for the GitHubBackend."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from git.remote import PushInfo

from devflow.forge.base import MRInfo
from devflow.forge.github import GitHubBackend


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
        ok_result = MagicMock()
        ok_result.flags = 0  # success: no ERROR bit set
        ok_result.summary = ""
        ok_result.new_rev_sha = "abc123"
        mock_remote.push.return_value = [ok_result]
        mock_repo.remotes.origin = mock_remote
        mock_repo_cls.return_value = mock_repo

        sha = backend.push("feature-branch", "main", "/path/to/repo")

    mock_remote.push.assert_called_once()
    assert sha is not None


def test_push_raises_on_error_flags() -> None:
    """push() raises RuntimeError when PushInfo has ERROR flag."""
    backend = GitHubBackend({"token": "abc", "repo": "owner/repo"})

    with patch("devflow.forge.github.Repo") as mock_repo_cls:
        mock_repo = MagicMock()
        mock_remote = MagicMock()
        error_result = MagicMock()
        error_result.flags = PushInfo.ERROR
        error_result.summary = " ! [rejected] HEAD -> branch (non-fast-forward)"
        mock_remote.push.return_value = [error_result]
        mock_repo.remotes.origin = mock_remote
        mock_repo_cls.return_value = mock_repo

        with pytest.raises(RuntimeError, match="push failed"):
            backend.push("branch", "main", "/path")


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
