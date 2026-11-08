"""Tests for github.checker: Rich Message rendering, notifications, and the check cycle."""

import html

from aiogram.types import InputRichMessage

import github.checker as checker
from github.checker import (
    RENDER_CACHE,
    _render_release_body_rich,
    _send_release_notification,
    notify_admin,
    run_check_cycle,
)

from conftest import make_release


# ---------------------------------------------------------------------------
# _render_release_body_rich (caching + fallback)
# ---------------------------------------------------------------------------


async def test_render_release_body_rich_empty():
    RENDER_CACHE.clear()
    assert await _render_release_body_rich(make_release(rid=9, body="")) == ""


async def test_render_release_body_rich_caches(monkeypatch):
    RENDER_CACHE.clear()
    calls = []

    def _fake_to_rich(body):
        calls.append(body)
        return "<b>hello</b>"

    monkeypatch.setattr(checker, "to_rich", _fake_to_rich)
    rel = make_release(rid=5, body="hello")
    first = await _render_release_body_rich(rel)
    second = await _render_release_body_rich(rel)
    assert first == "<b>hello</b>"
    assert second == first
    assert len(calls) == 1  # second call must hit the cache


async def test_render_release_body_rich_fallback(monkeypatch):
    RENDER_CACHE.clear()

    def _boom(_body):
        raise RuntimeError("x")

    monkeypatch.setattr(checker, "to_rich", _boom)
    rel = make_release(rid=7, body="<b>raw</b>")
    result = await _render_release_body_rich(rel)
    assert result == html.escape("<b>raw</b>")
    assert 7 not in RENDER_CACHE  # fallback is not cached


async def test_render_release_body_rich_preprocesses_html(monkeypatch):
    """Wrapper tags are stripped; Rich-compatible tags survive to_rich as-is."""
    RENDER_CACHE.clear()
    captured = {}

    def _fake_to_rich(body):
        captured["body"] = body
        # Mimic to_rich: drop any marker as-is (it would not touch real tags here
        # because they never reach it — they are hidden behind markers).
        return body

    monkeypatch.setattr(checker, "to_rich", _fake_to_rich)
    raw_body = '<div align="center"><img src="logo.png" alt="Logo"><h1 align="center">Title</h1></div>'
    rel = make_release(rid=11, body=raw_body)
    result = await _render_release_body_rich(rel)

    # to_rich saw markers, not the real tags.
    assert "<div" not in captured["body"]
    assert "<img" not in captured["body"]
    assert "align=" not in captured["body"]
    assert "\u27e6TAG0\u27e7" in captured["body"]
    # After restore, the original tags are back for Telegram to render.
    assert '<img src="logo.png" alt="Logo">' in result
    assert "<h1>Title</h1>" in result


async def test_render_release_body_rich_converts_details(monkeypatch):
    """<details><summary>T</summary>body</details> -> **T** + body."""
    RENDER_CACHE.clear()
    captured = {}

    def _fake_to_rich(body):
        captured["body"] = body
        return body

    monkeypatch.setattr(checker, "to_rich", _fake_to_rich)
    raw_body = "<details><summary>View changelog</summary>Some notes here.</details>"
    rel = make_release(rid=12, body=raw_body)
    await _render_release_body_rich(rel)

    assert "<details>" not in captured["body"]
    assert "<summary>" not in captured["body"]
    assert "**View changelog**" in captured["body"]
    assert "Some notes here." in captured["body"]


# ---------------------------------------------------------------------------
# _send_release_notification (Rich Message + optional back button)
# ---------------------------------------------------------------------------


async def test_send_release_notification_uses_rich_message(monkeypatch, fake_bot):
    RENDER_CACHE.clear()
    monkeypatch.setattr(checker, "to_rich", lambda _b: "<b>rendered</b>")
    rel = make_release(rid=3, tag="v1.0", name="R", body="hi")
    await _send_release_notification(fake_bot, 42, "owner/repo", rel)
    assert len(fake_bot.rich_sent) == 1
    chat_id, rich_msg, kwargs = fake_bot.rich_sent[0]
    assert chat_id == 42
    assert isinstance(rich_msg, InputRichMessage)
    assert "owner/repo" in rich_msg.html
    assert "<b>rendered</b>" in rich_msg.html
    assert "🚀" in rich_msg.html
    # Background notification still carries the translate toggle button.
    markup = kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].text == "🌐 Перевести"
    assert markup.inline_keyboard[0][0].callback_data == "reltr:3:0"


async def test_send_release_notification_with_back_button(monkeypatch, fake_bot):
    """Inline release view supplies repo_index -> 'back to repository' button below the toggle."""
    RENDER_CACHE.clear()
    monkeypatch.setattr(checker, "to_rich", lambda _b: "")
    rel = make_release(rid=3, tag="v1.0", body="")
    await _send_release_notification(fake_bot, 42, "owner/repo", rel, repo_index=7)
    assert len(fake_bot.rich_sent) == 1
    _, _, kwargs = fake_bot.rich_sent[0]
    markup = kwargs["reply_markup"]
    assert markup is not None
    assert markup.inline_keyboard[0][0].text == "🌐 Перевести"
    assert markup.inline_keyboard[1][0].text == "⬅️ К репозиторию"
    assert markup.inline_keyboard[1][0].callback_data == "repo:view:7"


async def test_send_release_notification_empty_body(monkeypatch, fake_bot):
    RENDER_CACHE.clear()
    monkeypatch.setattr(checker, "to_rich", lambda _b: "")
    rel = make_release(rid=3, tag="v1.0", name="v1.0", body="")
    await _send_release_notification(fake_bot, 1, "o/r", rel)
    assert len(fake_bot.rich_sent) == 1
    _, rich_msg, _ = fake_bot.rich_sent[0]
    # Header + link only; no body paragraph.
    assert rich_msg.html.count("\n\n") == 1


# ---------------------------------------------------------------------------
# notify_admin
# ---------------------------------------------------------------------------


async def test_notify_admin_disabled(fake_bot, monkeypatch):
    monkeypatch.setattr(checker, "ADMIN_USER_ID", 0)
    await notify_admin(fake_bot, "x")
    assert fake_bot.sent == []


async def test_notify_admin_sends(fake_bot, monkeypatch):
    monkeypatch.setattr(checker, "ADMIN_USER_ID", 99)
    await notify_admin(fake_bot, "hello")
    assert fake_bot.sent == [(99, "hello", {})]


# ---------------------------------------------------------------------------
# run_check_cycle integration
# ---------------------------------------------------------------------------


async def _run_cycle(monkeypatch, bot, subs, latest, recent, *, fetch_raises=False):
    import copy

    monkeypatch.setattr(checker, "load_subscriptions", lambda: subs)
    if fetch_raises:

        async def _boom(*_a, **_k):
            raise RuntimeError("boom")

        monkeypatch.setattr(checker, "fetch_latest_release", _boom)
        monkeypatch.setattr(checker, "fetch_last_n_releases", _boom)
    else:

        async def _latest(*_a, **_k):
            return latest

        async def _recent(*_a, **_k):
            return recent

        monkeypatch.setattr(checker, "fetch_latest_release", _latest)
        monkeypatch.setattr(checker, "fetch_last_n_releases", _recent)

    captured = {}

    async def _atomic(mutator):
        captured["mutator"] = mutator
        snap = copy.deepcopy(subs)
        mutator(snap)
        captured["snapshot"] = snap

    monkeypatch.setattr(checker, "atomic_update", _atomic)

    admin = []

    async def _admin(_b, text):
        admin.append(text)

    monkeypatch.setattr(checker, "notify_admin", _admin)
    monkeypatch.setattr(checker, "API_DELAY_SEC", 0)
    RENDER_CACHE.clear()
    monkeypatch.setattr(checker, "to_rich", lambda _b: "<b>rendered</b>")

    await run_check_cycle(bot)
    return captured, admin, bot.sent


def _repo_entry(name, last_release_id=None, notify_prerelease=False):
    return {
        "url": f"https://github.com/{name}",
        "name": name,
        "last_release_id": last_release_id,
        "cached_releases": [],
        "last_checked": "",
        "notify_prerelease": notify_prerelease,
    }


async def test_run_check_cycle_new_release_notifies_and_updates(monkeypatch, fake_bot):
    subs = {"users": {1: [_repo_entry("o/r", last_release_id=None)]}}
    latest = make_release(rid=10, tag="v1.0")
    captured, _admin, _sent = await _run_cycle(monkeypatch, fake_bot, subs, latest, [])
    assert len(fake_bot.rich_sent) == 1
    saved = captured["snapshot"]["users"][1][0]
    assert saved["last_release_id"] == 10


async def test_run_check_cycle_same_id_no_notify(monkeypatch, fake_bot):
    subs = {"users": {1: [_repo_entry("o/r", last_release_id=10)]}}
    latest = make_release(rid=10, tag="v1.0")
    captured, _admin, _sent = await _run_cycle(monkeypatch, fake_bot, subs, latest, [])
    assert fake_bot.rich_sent == []
    assert captured["snapshot"]["users"][1][0]["last_release_id"] == 10


async def test_run_check_cycle_prerelease_notified(monkeypatch, fake_bot):
    subs = {"users": {1: [_repo_entry("o/r", last_release_id=5, notify_prerelease=True)]}}
    latest = make_release(rid=10, tag="v1.0")
    recent = [
        make_release(rid=20, tag="v2.0-pre", name="v2.0-pre", prerelease=True),
        make_release(rid=10, tag="v1.0", name="v1.0", prerelease=False),
    ]
    captured, _admin, _sent = await _run_cycle(monkeypatch, fake_bot, subs, latest, recent)
    assert len(fake_bot.rich_sent) == 1
    assert "v2.0-pre" in fake_bot.rich_sent[0][1].html
    assert captured["snapshot"]["users"][1][0]["last_release_id"] == 20


async def test_run_check_cycle_prerelease_ignored(monkeypatch, fake_bot):
    subs = {"users": {1: [_repo_entry("o/r", last_release_id=5, notify_prerelease=False)]}}
    latest = make_release(rid=10, tag="v1.0")
    recent = [
        make_release(rid=20, tag="v2.0-pre", name="v2.0-pre", prerelease=True),
        make_release(rid=10, tag="v1.0", name="v1.0", prerelease=False),
    ]
    captured, _admin, _sent = await _run_cycle(monkeypatch, fake_bot, subs, latest, recent)
    assert len(fake_bot.rich_sent) == 1
    assert "v1.0" in fake_bot.rich_sent[0][1].html
    assert "v2.0-pre" not in fake_bot.rich_sent[0][1].html
    assert captured["snapshot"]["users"][1][0]["last_release_id"] == 10


async def test_run_check_cycle_fetch_failure_notifies_admin(monkeypatch, fake_bot):
    subs = {"users": {1: [_repo_entry("o/r", last_release_id=None)]}}
    _captured, admin, _sent = await _run_cycle(monkeypatch, fake_bot, subs, None, [], fetch_raises=True)
    assert fake_bot.rich_sent == []
    assert admin  # notify_admin was called
