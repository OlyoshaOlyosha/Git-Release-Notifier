"""Low-level interactions with the public GitHub REST API."""

from __future__ import annotations

import aiohttp

from core.config import GITHUB_TOKEN
from core.models import ReleaseInfo

GITHUB_API_BASE = "https://api.github.com"


async def fetch_repo_info(owner: str, repo: str) -> dict:
    """Fetch basic repository information (e.g., full_name)."""
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
    async with aiohttp.ClientSession(headers=headers) as session, session.get(url) as resp:
        resp.raise_for_status()
        return await resp.json()


async def fetch_latest_release(owner: str, repo: str) -> ReleaseInfo | None:
    """Fetch the latest (non-draft, non-prerelease) release.

    Returns None if no such release exists.
    """
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/releases/latest"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
    async with aiohttp.ClientSession(headers=headers) as session, session.get(url) as resp:
        if resp.status == 404:
            return None
        resp.raise_for_status()
        data = await resp.json()
        return _extract_release_info(data)


async def fetch_last_n_releases(owner: str, repo: str, n: int = 3) -> list[ReleaseInfo]:
    """Fetch the last N releases (excluding drafts and prereleases)."""
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/releases?per_page={n}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
    async with aiohttp.ClientSession(headers=headers) as session, session.get(url) as resp:
        resp.raise_for_status()
        data = await resp.json()
        return [_extract_release_info(item) for item in data if not item.get("draft") and not item.get("prerelease")]


def _extract_release_info(api_data: dict) -> ReleaseInfo:
    """Extract only the fields we need from the GitHub API response."""
    return ReleaseInfo(
        id=api_data["id"],
        tag_name=api_data["tag_name"],
        name=api_data.get("name") or api_data["tag_name"],
        html_url=api_data["html_url"],
        body=api_data.get("body") or "",
        published_at=api_data.get("published_at", ""),
    )
