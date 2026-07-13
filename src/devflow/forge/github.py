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
        # Return the current HEAD sha as the pushed commit identifier.
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
            raise ValueError(
                "GitHubBackend requires a repo (set GITHUB_REPO to 'owner/repo')"
            )

        headers = {
            "Authorization": f"token {self._token}",
            "Accept": "application/vnd.github+json",
        }
        base_url = f"{self._api_url}/repos/{self._repo}"
        owner = self._repo.split("/")[0]

        with httpx.Client(timeout=30.0) as client:
            # Check for an existing PR (idempotency).
            list_resp = client.get(
                f"{base_url}/pulls",
                params={"head": f"{owner}:{branch}", "state": "open"},
                headers=headers,
            )
            list_resp.raise_for_status()
            existing = list_resp.json()
            if existing:
                pr = existing[0]
                logger.info(
                    "GitHub: existing PR #%s found for branch %s",
                    pr["number"],
                    branch,
                )
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
