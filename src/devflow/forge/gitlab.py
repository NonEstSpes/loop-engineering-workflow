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
            # GitPython PushInfo flags: ERROR=1024, REJECTED, REMOTE_REJECTED, etc.
            from git.remote import PushInfo

            if result.flags & PushInfo.ERROR:
                raise RuntimeError(
                    f"GitLab push failed (flags={result.flags}): {result.summary}"
                )
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
