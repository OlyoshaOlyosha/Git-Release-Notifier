"""Background task that periodically checks all tracked repositories for new releases."""

from __future__ import annotations

import asyncio
import html
import logging
import re
from collections import OrderedDict
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from aiogram.types import InputRichMessage
from tg_rich_converter import to_rich

from core.config import ADMIN_USER_ID, API_DELAY_SEC, CHECK_INTERVAL_SEC
from core.models import (
    CachedReleaseInfo,
    ReleaseInfo,
    UserSettings,
    atomic_update,
    get_user_settings,
    load_subscriptions,
)
from github.github_api import fetch_last_n_releases, fetch_latest_release
from github.translate import translate_text
from ui.keyboards import release_translate_keyboard

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)

# Cache rendered release bodies by release id to avoid re-rendering on every cycle.
RENDER_CACHE: dict[int, str] = {}
# Translated bodies per (release id, language); original lives in RENDER_CACHE.
TRANSLATED_CACHE: OrderedDict[tuple[int, str], str] = OrderedDict()
# Translated release names per (release id, language).
NAME_CACHE: dict[tuple[int, str], str] = {}
# Recent notifications per release id, enabling the translate/original toggle
# to re-render the message without re-fetching GitHub. Bounded, oldest evicted.
RELEASE_CACHE: OrderedDict[int, dict] = OrderedDict()
_CACHE_LIMIT = 200


def _cache_put(cache: OrderedDict, key, value) -> None:
    """Insert into a bounded cache, evicting the oldest entry on overflow."""
    if len(cache) >= _CACHE_LIMIT:
        cache.popitem(last=False)
    cache[key] = value


async def notify_admin(bot: Bot, text: str) -> None:
    """Send a diagnostic message to the admin (no-op if ADMIN_USER_ID is 0)."""
    if ADMIN_USER_ID == 0:
        return
    try:
        await bot.send_message(ADMIN_USER_ID, text)
    except Exception:
        logger.warning("Failed to notify admin: %s", text)


async def run_check_cycle(bot: Bot) -> None:
    """Perform a single check cycle for all repos: fetch releases, update cache, notify.

    Reads the subscription snapshot once for enumeration, fetches releases
    (network, outside any lock), then persists all cache/last_release updates in
    a single serialized read-modify-write. Notifications are sent after the
    lock is released so the bot is never blocked on I/O while holding it.
    """
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

        # Collected across all repos so we can persist in one atomic write.
        updates: list[dict] = []  # {"uid", "name", "cached", "ts", "new_id"}
        to_notify: list[tuple[int, str, ReleaseInfo]] = []  # (uid, name, best_release)

        for name, info in unique_repos.items():
            owner, repo_name = name.split("/", 1)

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
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

                # Update every user who subscribes to this repo
                for uid in info["users"]:
                    user_repos = users.get(uid)
                    if not user_repos:
                        continue
                    for repo in user_repos:
                        if repo.get("name") != name:
                            continue

                        # Build notification candidates: the latest release, plus pre-releases if enabled
                        candidates = []
                        if latest_release:
                            candidates.append(latest_release)
                        if repo.get("notify_prerelease", False):
                            for r in recent_releases:
                                if r.get("prerelease"):
                                    candidates.append(r)

                        new_id = None
                        if candidates:
                            best = max(candidates, key=lambda r: r["id"])
                            candidate_id = best["id"]
                            old_id = repo.get("last_release_id")
                            if old_id is None or old_id < candidate_id:
                                new_id = candidate_id
                                logger.info("New release detected for %s (id=%d), notifying user %d", name, new_id, uid)
                                to_notify.append((uid, name, best))
                            else:
                                logger.info("No new release for %s", name)

                        updates.append(
                            {
                                "uid": uid,
                                "name": name,
                                "cached": cached,
                                "ts": ts,
                                "new_id": new_id,
                            }
                        )

            await asyncio.sleep(API_DELAY_SEC)

        # Persist all cache/last_release updates in a single serialized write.
        def _apply(subs_snapshot: dict) -> None:
            for u in updates:
                uid = u["uid"]
                user_repos = subs_snapshot["users"].get(uid, [])
                for repo in user_repos:
                    if repo.get("name") == u["name"]:
                        repo["cached_releases"] = u["cached"]
                        repo["last_checked"] = u["ts"]
                        if u["new_id"] is not None:
                            repo["last_release_id"] = u["new_id"]
                        break

        if updates:
            await atomic_update(_apply)

        # Send notifications outside the lock so I/O never blocks the lock.
        for uid, name, best in to_notify:
            try:
                settings = get_user_settings(subs, uid)
                await _send_release_notification(bot, uid, name, best, settings=settings)
                logger.info("Notification sent to user %d for %s", uid, name)
            except Exception as e:
                logger.warning("Could not notify user %d: %s", uid, e)

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


# Layout tags that Telegram Rich Messages don't support; drop them but keep inner text.
_WRAPPER_TAG_RE = re.compile(
    r"</?(?:div|span|section|article|header|footer|main|aside|nav|figure|figcaption|picture|source|video|audio|iframe)(?:\s[^>]*)?>",
    re.IGNORECASE,
)
_ALIGN_ATTR_RE = re.compile(r'\s+align="[^"]*"', re.IGNORECASE)

# Rich-compatible tags: hide them from to_rich (which escapes any raw HTML) and
# restore them in the final output.
_RICH_TAG_RE = re.compile(
    r"</?(?:h[1-6]|b|strong|i|em|u|s|strike|del|code|pre|blockquote|a|br|ul|ol|li|"
    r"table|thead|tbody|tr|th|td|img|hr)\b[^>]*>",
    re.IGNORECASE,
)
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

# <details><summary>T</summary>body</details> -> **T**\n\nbody.
# Rich Messages' HTML support for <details> isn't documented; a bold summary
# preserves the visual hierarchy without relying on undocumented behavior.
_DETAILS_BLOCK_RE = re.compile(
    r"<details[^>]*>\s*<summary[^>]*>(.*?)</summary>(.*?)</details>",
    re.IGNORECASE | re.DOTALL,
)

_TAG_MARKER = "\u27e6TAG{}\u27e7"


def _preprocess_release_body(body: str) -> tuple[str, list[str]]:
    """Shrink GitHub-flavoured HTML in a release body to Markdown that to_rich handles.

    ``tg-rich-converter`` escapes any raw HTML to entities, which then appear as
    literal text in Telegram. To let Rich Messages render headings, images,
    links, code, etc., those tags are swapped for opaque markers before to_rich
    runs and restored afterwards by ``_restore_rich_tags``.

    Returns ``(transformed_body, saved_tags)``.
    """
    body = _DETAILS_BLOCK_RE.sub(lambda m: f"\n\n**{m.group(1).strip()}**\n\n{m.group(2).strip()}\n\n", body)
    body = _WRAPPER_TAG_RE.sub("", body)
    body = _ALIGN_ATTR_RE.sub("", body)
    # Normalize Markdown images to <img> so they go through the same protection.
    body = _MD_IMAGE_RE.sub(lambda m: f'<img src="{m.group(2)}" alt="{m.group(1)}">', body)

    saved: list[str] = []

    def _save(m: re.Match) -> str:
        saved.append(m.group(0))
        return _TAG_MARKER.format(len(saved) - 1)

    body = _RICH_TAG_RE.sub(_save, body)
    return body, saved


def _restore_rich_tags(text: str, saved: list[str]) -> str:
    for i, tag in enumerate(saved):
        text = text.replace(_TAG_MARKER.format(i), tag)
    return text


async def _render_release_body_rich(release: ReleaseInfo, lang: str | None = None) -> str:
    """Convert a release Markdown body to Rich HTML via tg-rich-converter, with caching.

    Conversion is local, except that a non-empty ``lang`` first translates the
    preprocessed Markdown (with Rich tags hidden as opaque markers, so Google
    cannot translate the tags themselves). Originals are cached in RENDER_CACHE,
    translations in TRANSLATED_CACHE per (release id, lang). Falls back to
    escaped raw text if the converter raises.
    """
    body = release.get("body") or ""
    if not body:
        return ""
    rid = release.get("id")
    if lang is None:
        if rid and rid in RENDER_CACHE:
            return RENDER_CACHE[rid]
    elif (rid, lang) in TRANSLATED_CACHE:
        return TRANSLATED_CACHE[(rid, lang)]
    try:
        cleaned, saved = _preprocess_release_body(body)
        if lang:
            cleaned = await translate_text(cleaned, lang)
            missing = [i for i in range(len(saved)) if _TAG_MARKER.format(i) not in cleaned]
            if missing:
                logger.warning("Translation lost %d tag markers for release %s: %s", len(missing), rid, missing)
        rich_html = _restore_rich_tags(to_rich(cleaned), saved)
    except Exception as e:  # noqa: BLE001 - converter may raise on malformed markdown
        logger.warning("Rich conversion failed for release %s: %s", rid, e)
        return html.escape(body)
    if rid:
        if lang is None:
            RENDER_CACHE[rid] = rich_html
        else:
            _cache_put(TRANSLATED_CACHE, (rid, lang), rich_html)
    return rich_html


async def _release_display_name(release: ReleaseInfo, lang: str | None = None) -> str:
    """Return the release name, translated to ``lang`` if requested (cached)."""
    name = release.get("name")
    if not lang or not name:
        return name or ""
    key = (release.get("id"), lang)
    if key not in NAME_CACHE:
        NAME_CACHE[key] = await translate_text(name, lang)
    return NAME_CACHE[key]


async def build_release_html(release: ReleaseInfo, repo_name: str, header: str, lang: str | None = None) -> str:
    """Compose the full Rich HTML for a release message (original or translated)."""
    rich_body = await _render_release_body_rich(release, lang)
    release_name = await _release_display_name(release, lang)
    name_suffix = ""
    if release_name and release_name != release["tag_name"]:
        name_suffix = f" — {html.escape(release_name)}"
    parts = [
        f"<b>{header} {html.escape(repo_name)}</b>",
        f'<a href="{html.escape(release["html_url"], quote=True)}">{html.escape(release["tag_name"])}</a>'
        + name_suffix,
    ]
    if rich_body:
        parts.append(rich_body)
    elif release.get("body"):
        parts.append(html.escape(release["body"]))
    return "\n\n".join(parts)


async def _send_release_notification(
    bot: Bot,
    uid: int,
    repo_name: str,
    release: ReleaseInfo,
    header: str = "🚀 Новый релиз",
    repo_index: int | None = None,
    settings: UserSettings | None = None,
) -> None:
    """Send a release notification as a Rich Message with a translate toggle button.

    When ``repo_index`` is provided (release opened from the repo detail view),
    a "back to repository" button is added below the translate button.

    If ``settings`` enables ``auto_translate``, the message is rendered in the
    user's language and the button offers the original; otherwise it arrives
    untranslated with a translate button. The release is remembered in
    RELEASE_CACHE so the toggle never re-fetches GitHub. If the body exceeds
    Telegram's 32 768-char limit (rare), the API rejects the message; the
    caller logs the failure.
    """
    settings = settings or {}
    lang = settings.get("translate_lang") if settings.get("auto_translate") else None
    full_html = await build_release_html(release, repo_name, header, lang)

    rid = release.get("id")
    if rid:
        _cache_put(
            RELEASE_CACHE,
            rid,
            {"release": release, "repo_name": repo_name, "header": header, "repo_index": repo_index},
        )

    await bot.send_rich_message(
        chat_id=uid,
        rich_message=InputRichMessage(html=full_html),
        reply_markup=release_translate_keyboard(rid, translated=lang is not None, repo_index=repo_index),
    )
