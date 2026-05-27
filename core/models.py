"""Data models and JSON persistence layer.

Provides TypedDicts for subscriptions and release info, plus atomic
load/save functions for the subscriptions file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

from core.config import SUBSCRIPTIONS_FILE


class CachedReleaseInfo(TypedDict):
    """A lightweight release entry cached per repository to avoid repeated API calls."""

    tag_name: str
    name: str
    html_url: str
    published_at: str  # ISO 8601 date string


class RepoEntry(TypedDict):
    """A single tracked repository for a user."""

    url: str  # full URL, e.g. "https://github.com/owner/repo"
    name: str  # "owner/repo", fetched from GitHub API on add
    last_release_id: int | None  # ID of last known release; None if never checked
    cached_releases: list[CachedReleaseInfo]  # last 3 releases, refreshed by background checker
    last_checked: str  # ISO 8601 timestamp of last successful check, or "" if never


class Subscriptions(TypedDict):
    """Root structure of the subscriptions file."""

    users: dict[int, list[RepoEntry]]  # key: Telegram user_id


class ReleaseInfo(TypedDict):
    """Full release information extracted from GitHub API (used for fresh fetches)."""

    id: int
    tag_name: str
    name: str
    html_url: str
    body: str
    published_at: str  # ISO 8601 date string


def load_subscriptions() -> Subscriptions:
    """Load subscriptions from the JSON file.

    Returns a default empty structure if the file does not exist or is invalid.
    Converts string user IDs back to integers to match the internal model.
    """
    path = Path(SUBSCRIPTIONS_FILE)
    if not path.exists():
        return {"users": {}}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if "users" not in data or not isinstance(data["users"], dict):
                return {"users": {}}
            # Convert string keys to int to avoid mismatch with Telegram user IDs
            data["users"] = {int(uid): repos for uid, repos in data["users"].items()}
            return data
    except (json.JSONDecodeError, OSError, ValueError):
        return {"users": {}}


def save_subscriptions(data: Subscriptions) -> None:
    """Atomically write subscriptions to the JSON file using a temporary file and rename."""
    path = Path(SUBSCRIPTIONS_FILE)
    tmp_path = path.with_suffix(".tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp_path.replace(path)
    except OSError:
        # If rename fails, try to clean up the temp file
        tmp_path.unlink(missing_ok=True)
