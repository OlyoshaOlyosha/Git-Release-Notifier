"""Data models and JSON persistence layer.

Provides TypedDicts for subscriptions and release info, plus atomic
load/save functions for the subscriptions file.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Callable, TypedDict

from core.config import SUBSCRIPTIONS_FILE, TARGET_LANG

# Serializes all read-modify-write mutations of subscriptions.json so that
# interleaved writes (background checker vs. user commands) cannot clobber
# each other. Held only around load+mutate+save, never around network calls.
SUBS_LOCK = asyncio.Lock()


async def atomic_update(mutator: Callable[[Subscriptions], None]) -> None:
    """Apply a mutation to the subscriptions with a serialized read-modify-write.

    The mutator receives the freshly loaded subscriptions dict and mutates it
    in place; the result is then persisted atomically. The lock is released
    before any network I/O, so callers must not perform fetches inside mutator.
    """
    async with SUBS_LOCK:
        subs = load_subscriptions()
        mutator(subs)
        save_subscriptions(subs)


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
    notify_prerelease: bool  # notify on pre-releases too; default False


class Subscriptions(TypedDict):
    """Root structure of the subscriptions file."""

    users: dict[int, list[RepoEntry]]  # key: Telegram user_id
    settings: dict[int, UserSettings]  # optional; per-user bot settings


class UserSettings(TypedDict):
    """Per-user bot settings stored under ``settings[user_id]``."""

    auto_translate: bool  # translate release notifications automatically; default False
    translate_lang: str  # target language code; default comes from TARGET_LANG env


DEFAULT_USER_SETTINGS: UserSettings = {
    "auto_translate": False,
    "translate_lang": TARGET_LANG or "ru",
}


def get_user_settings(subs: Subscriptions, uid: int) -> UserSettings:
    """Return the user's settings merged over defaults (no writes)."""
    stored = subs.get("settings", {}).get(uid, {})
    return {**DEFAULT_USER_SETTINGS, **stored}


def set_user_settings(subs: Subscriptions, uid: int, **kwargs) -> None:
    """Persist a partial settings update (mutates ``subs`` in place)."""
    subs.setdefault("settings", {}).setdefault(uid, {}).update(kwargs)


class ReleaseInfo(TypedDict):
    """Full release information extracted from GitHub API (used for fresh fetches)."""

    id: int
    tag_name: str
    name: str
    html_url: str
    body: str
    body_html: str  # pre-rendered release body (subset converted for Telegram)
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
            settings = data.get("settings")
            if isinstance(settings, dict):
                data["settings"] = {int(uid): s for uid, s in settings.items()}
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
