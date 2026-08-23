"""Background task that periodically checks all tracked repositories for new releases."""

from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import TYPE_CHECKING

from core.config import ADMIN_USER_ID, API_DELAY_SEC, CHECK_INTERVAL_SEC
from core.models import CachedReleaseInfo, ReleaseInfo, atomic_update, load_subscriptions
from github.github_api import fetch_last_n_releases, fetch_latest_release, render_markdown

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)

# Cache rendered release bodies by release id to avoid re-rendering on every cycle.
RENDER_CACHE: dict[int, str] = {}


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
                await _send_release_notification(bot, uid, name, best)
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


class _TgHtmlConverter(HTMLParser):
    """Convert GitHub's pre-rendered ``body_html`` to Telegram-supported HTML.

    Tags outside Telegram's allowed subset are dropped while their inner text is
    preserved (except ``img`` and other unknown tags, where only text survives).
    """

    # Allowed tags mapped to their Telegram equivalent tag name.
    _ALLOWED_MAP = {
        "b": "b",
        "strong": "b",
        "i": "i",
        "em": "i",
        "u": "u",
        "s": "s",
        "strike": "s",
        "del": "s",
        "blockquote": "blockquote",
        "pre": "pre",
        "a": "a",
        "br": "br",
        "code": "code",
        "li": "li",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._inside_pre = 0
        self._a_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str]]) -> None:
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._out.append("\n<b>")
            return
        if tag == "img":
            src = dict(attrs).get("src", "")
            alt = dict(attrs).get("alt", "")
            label = html.escape(f"🖼 {alt}") if alt else "🖼 изображение"
            if self._a_depth > 0:
                self._out.append(label)
            else:
                self._out.append(f'<a href="{html.escape(src, quote=True)}">{label}</a>')
            return
        tg = self._ALLOWED_MAP.get(tag)
        if tg is None:
            return  # dropped tag; inner text is preserved by handle_data
        if tag == "li":
            self._out.append("\n• ")
            return
        if tg == "code" and self._inside_pre > 0:
            return  # already inside <pre>; don't wrap in <code>
        if tg == "a":
            self._a_depth += 1
            href = dict(attrs).get("href", "")
            self._out.append(f'<a href="{html.escape(href, quote=True)}">')
        elif tg == "br":
            self._out.append("\n")
        else:
            self._out.append(f"<{tg}>")
        if tg == "pre":
            self._inside_pre += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._out.append("</b>\n")
            return
        if tag == "img":
            return
        tg = self._ALLOWED_MAP.get(tag)
        if tg is None:
            return
        if tag == "li":
            return
        if tg == "code" and self._inside_pre > 0:
            return
        if tg == "br":
            return
        if tg == "a":
            self._a_depth = max(0, self._a_depth - 1)
            self._out.append("</a>")
        else:
            self._out.append(f"</{tg}>")
        if tg == "pre":
            self._inside_pre = max(0, self._inside_pre - 1)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str]]) -> None:
        # Self-closing forms: <br/>, <img/>, <hr/>, etc.
        if tag == "br":
            self._out.append("\n")
            return
        if tag == "img":
            src = dict(attrs).get("src", "")
            alt = dict(attrs).get("alt", "")
            label = html.escape(f"🖼 {alt}") if alt else "🖼 изображение"
            if self._a_depth > 0:
                self._out.append(label)
            else:
                self._out.append(f'<a href="{html.escape(src, quote=True)}">{label}</a>')
            return
        # Any other self-closing tag is dropped (kept tags like <a/> don't carry content).

    def handle_data(self, data: str) -> None:
        self._out.append(html.escape(data))


def _github_html_to_telegram(html_text: str) -> str:
    """Convert GitHub release HTML to a Telegram-safe HTML string."""
    parser = _TgHtmlConverter()
    parser.feed(html_text)
    parser.close()
    result = "".join(parser._out)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


async def _render_release_body(release: ReleaseInfo) -> str:
    """Render a release body (GFM markdown) to Telegram-safe HTML, with caching.

    GitHub's release object only carries raw ``body`` (GFM markdown), so we render
    it via GitHub's ``/markdown`` endpoint, then sanitize to Telegram's HTML subset.
    Failures fall back to escaping the raw markdown (uncached).
    """
    body = release.get("body") or ""
    if not body:
        return ""
    rid = release.get("id")
    if rid and rid in RENDER_CACHE:
        return RENDER_CACHE[rid]
    try:
        html_text = await render_markdown(body)
    except Exception as e:  # noqa: BLE001 - network/render can fail in many ways; fall back gracefully
        logger.warning("markdown render failed: %s", e)
        return html.escape(body)
    tg = _github_html_to_telegram(html_text)
    if rid:
        RENDER_CACHE[rid] = tg
    return tg


def _split_html_safe(text: str, limit: int = 4000) -> list[str]:
    """Split HTML into chunks of at most `limit` chars.

    Cutting happens only at tag boundaries; any tags still open at the end of a
    chunk are closed and re-opened at the start of the next chunk so markup is
    never broken across messages. A run of bare text with no tags that still
    exceeds `limit` is hard-split by characters as a last resort.
    """
    tokens = re.split(r"(<[^>]+>)", text)
    chunks: list[str] = []
    current = ""
    open_tags: list[tuple[str, str]] = []  # (full opening tag, tag name)

    def _carry() -> str:
        return "".join(opening for opening, _ in open_tags)

    def _close() -> str:
        return "".join(f"</{name}>" for _, name in reversed(open_tags))

    for tok in tokens:
        if not tok:
            continue
        stripped = tok.strip()
        if stripped.startswith("<"):
            is_closing = stripped.startswith("</")
            name_part = stripped[2:-1] if is_closing else stripped[1:-1]
            parts = name_part.split()
            name = parts[0].rstrip("/").lower() if parts else ""
            if is_closing:
                for idx in range(len(open_tags) - 1, -1, -1):
                    if open_tags[idx][1] == name:
                        del open_tags[idx]
                        break
                current += tok
            else:
                self_closing = stripped.endswith("/>") or name in ("br", "img")
                if self_closing:
                    current += tok
                else:
                    if len(current) + len(tok) > limit and current:
                        chunks.append(current + _close())
                        current = _carry()
                    open_tags.append((tok, name))
                    current += tok
        else:
            if len(current) + len(tok) > limit and current:
                chunks.append(current + _close())
                current = _carry()
            if len(current) + len(tok) > limit:
                carried = _carry()
                room = limit - len(carried)
                if room <= 0:
                    if current:
                        chunks.append(current)
                    step = max(1, limit)
                    for i in range(0, len(tok), step):
                        chunks.append(tok[i : i + step])
                    open_tags.clear()
                    current = ""
                else:
                    for i in range(0, len(tok), room):
                        chunks.append(carried + tok[i : i + room] + _close())
                    current = carried
            else:
                current += tok

    if current:
        chunks.append(current)
    return chunks


async def _send_release_notification(
    bot: Bot, uid: int, repo_name: str, release: ReleaseInfo, header: str = "🚀 Новый релиз"
) -> None:
    """Format and send a release notification, splitting into multiple messages if needed."""
    rendered = await _render_release_body(release)
    text = _format_release_notification(repo_name, release, rendered_body=rendered, header=header)
    for chunk in _split_html_safe(text, limit=4000):
        await bot.send_message(uid, chunk, parse_mode="HTML", disable_web_page_preview=True)


def _format_release_notification(
    repo_name: str, release: ReleaseInfo, rendered_body: str = "", header: str = "🚀 Новый релиз"
) -> str:
    """Format a notification message for a new release (Russian)."""
    parts = [
        f"{header} <b>{repo_name}</b>",
        f"<a href='{release['html_url']}'>{release['tag_name']}</a>"
        + (f" — {release['name']}" if release.get("name") and release["name"] != release["tag_name"] else ""),
    ]
    if rendered_body:
        parts.append(rendered_body)
    elif release.get("body"):
        parts.append(html.escape(release["body"]))
    return "\n\n".join(parts)
