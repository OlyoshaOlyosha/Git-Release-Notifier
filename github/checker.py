"""Background task that periodically checks all tracked repositories for new releases."""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from core.config import ADMIN_USER_ID, API_DELAY_SEC, CHECK_INTERVAL_SEC
from core.models import CachedReleaseInfo, ReleaseInfo, load_subscriptions, save_subscriptions
from github.github_api import fetch_last_n_releases, fetch_latest_release

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)


async def notify_admin(bot: Bot, text: str) -> None:
    """Send a diagnostic message to the admin (no-op if ADMIN_USER_ID is 0)."""
    if ADMIN_USER_ID == 0:
        return
    try:
        await bot.send_message(ADMIN_USER_ID, text)
    except Exception:
        logger.warning("Failed to notify admin: %s", text)


async def run_check_cycle(bot: Bot) -> None:
    """Perform a single check cycle for all repos: fetch releases, update cache, notify."""
    logger.info("Starting background check cycle")

    try:
        subs = load_subscriptions()
        users = subs.get("users", {})

        # Build a unique set of repos across all users
        unique_repos: dict[str, dict] = {}  # name -> {url, users}
        for uid, repo_list in users.items():
            for repo in repo_list:
                name = repo.get("name")
                if not name:
                    continue
                if name not in unique_repos:
                    unique_repos[name] = {"url": repo["url"], "users": []}
                unique_repos[name]["users"].append(uid)

        for name, info in unique_repos.items():
            owner, repo_name = name.split("/", 1)
            repo_modified = False

            # Determine whether any subscriber wants pre-release notifications for this repo
            notify = False
            for uid in info["users"]:
                for r in users.get(uid, []):
                    if r.get("name") == name and r.get("notify_prerelease", False):
                        notify = True
                        break
                if notify:
                    break

            logger.info("Checking %s", name)
            try:
                latest_release = await fetch_latest_release(owner, repo_name)
                recent_releases = await fetch_last_n_releases(owner, repo_name, 3, include_prerelease=notify)
            except Exception as e:
                logger.warning("Failed to fetch releases for %s: %s", name, e)
                await notify_admin(bot, f"GitHub API fetch failed for {name}: {e}")
            else:
                # Build lightweight cached list from successfully fetched releases
                cached = [
                    CachedReleaseInfo(
                        tag_name=r["tag_name"],
                        name=r["name"],
                        html_url=r["html_url"],
                        published_at=r["published_at"],
                    )
                    for r in recent_releases
                ]

                # Update every user who subscribes to this repo
                for uid in info["users"]:
                    user_repos = users.get(uid)
                    if not user_repos:
                        continue
                    for repo in user_repos:
                        if repo.get("name") == name:
                            repo["cached_releases"] = cached
                            repo["last_checked"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                            repo_modified = True

                            # Build notification candidates: the latest release, plus pre-releases if enabled
                            candidates = []
                            if latest_release:
                                candidates.append(latest_release)
                            if repo.get("notify_prerelease", False):
                                for r in recent_releases:
                                    if r.get("prerelease"):
                                        candidates.append(r)

                            if candidates:
                                best = max(candidates, key=lambda r: r["id"])
                                new_id = best["id"]
                                old_id = repo.get("last_release_id")
                                if old_id is None or old_id < new_id:
                                    logger.info(
                                        "New release detected for %s (id=%d), notifying user %d", name, new_id, uid
                                    )
                                    repo["last_release_id"] = new_id
                                    msg = _format_release_notification(repo["name"], best)
                                    try:
                                        await bot.send_message(
                                            uid,
                                            msg,
                                            parse_mode="HTML",
                                            disable_web_page_preview=True,
                                        )
                                        logger.info("Notification sent to user %d for %s", uid, name)
                                    except Exception as e:
                                        logger.warning("Could not notify user %d: %s", uid, e)
                                else:
                                    logger.info("No new release for %s", name)

            if repo_modified:
                save_subscriptions(subs)

            await asyncio.sleep(API_DELAY_SEC)

    except Exception:
        logger.exception("Error in background checker loop")

    logger.info("Background check cycle finished")


async def background_checker(bot: Bot) -> None:
    """Infinite loop that checks all unique repos and notifies subscribed users about new releases.

    For each unique repository, fetches the latest release to detect new ones
    and also refreshes the cached list of the last 3 releases.
    """
    # Run the first check cycle immediately on startup, then keep hourly cadence.
    await run_check_cycle(bot)

    while True:
        await asyncio.sleep(CHECK_INTERVAL_SEC)

        await run_check_cycle(bot)


def _format_release_notification(repo_name: str, release: ReleaseInfo) -> str:
    """Format a notification message for a new release (Russian)."""
    return (
        f"🚀 Новый релиз <b>{repo_name}</b>!\n"
        f"<a href='{release['html_url']}'>{release['tag_name']}</a>\n\n"
        f"{html.escape(release['body'][:500])}"  # truncate body to avoid oversized messages, then escape HTML
    )
