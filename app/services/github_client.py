"""Thin async wrapper around GitHub's issues API.

drift_agent.github already builds a SYNC httpx.Client for read-only repo
fetches. Issue creation is a write so we use an async client here with the
same auth shape (Bearer GITHUB_TOKEN).
"""
from __future__ import annotations

import os

import httpx
import structlog

log = structlog.get_logger()

GITHUB_API = "https://api.github.com"


class GitHubClient:
    def __init__(self, token: str | None = None, *, base_url: str = GITHUB_API) -> None:
        self._token = token if token is not None else os.environ.get("GITHUB_TOKEN", "")
        self._base_url = base_url
        self._client = httpx.AsyncClient(timeout=20.0)

    def _headers(self) -> dict[str, str]:
        h = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        }
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    async def create_issue(
        self,
        *,
        owner: str,
        repo: str,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> dict:
        """POST /repos/{owner}/{repo}/issues.

        Returns the parsed GitHub response on 2xx, raises on non-2xx so the
        caller can mark the proposal failed with the upstream error.
        """
        if not self._token:
            raise RuntimeError("GITHUB_TOKEN is empty — cannot create issues")
        payload: dict = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        url = f"{self._base_url}/repos/{owner}/{repo}/issues"
        r = await self._client.post(url, headers=self._headers(), json=payload)
        if r.status_code >= 300:
            log.error(
                "github_create_issue_failed",
                status=r.status_code,
                body=r.text[:400],
                owner=owner,
                repo=repo,
            )
            raise RuntimeError(f"GitHub {r.status_code} on POST {url}: {r.text[:300]}")
        return r.json()

    async def close(self) -> None:
        await self._client.aclose()
