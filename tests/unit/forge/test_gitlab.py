"""Unit tests for the GitLabBackend."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from git.remote import PushInfo

from devflow.forge.base import MRInfo
from devflow.forge.gitlab import GitLabBackend


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
        ok_result = MagicMock()
        ok_result.flags = 0  # success: no ERROR bit set
        ok_result.summary = ""
        mock_remote.push.return_value = [ok_result]
        mock_repo.remotes.origin = mock_remote
        mock_repo.head.commit.hexsha = "def456"
        mock_repo_cls.return_value = mock_repo

        sha = backend.push("feature-branch", "main", "/path/to/repo")

    mock_remote.push.assert_called_once()
    assert sha == "def456"


def test_push_raises_on_error_flags() -> None:
    """push() raises RuntimeError when PushInfo has ERROR flag."""
    backend = GitLabBackend({"token": "abc", "project_id": 123})

    with patch("devflow.forge.gitlab.Repo") as mock_repo_cls:
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
