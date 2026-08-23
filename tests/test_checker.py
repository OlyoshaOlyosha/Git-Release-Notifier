"""Tests for github.checker: formatting, HTML conversion, splitting, rendering, and the check cycle."""

import html
import re

import pytest

import github.checker as checker
from github.checker import (
    RENDER_CACHE,
    _format_release_notification,
    _github_html_to_telegram,
    _render_release_body,
    _send_release_notification,
    _split_html_safe,
    notify_admin,
    run_check_cycle,
)

from conftest import make_release


def assert_balanced(html_text: str) -> None:
    """Assert every opened tag (except void tags) is closed in the given HTML."""
    void = {"br", "img"}
    stack: list[str] = []
    for tag in re.findall(r"<\/?[a-zA-Z][^>]*\/?>", html_text):
        if tag.startswith("</"):
            name = tag[2:-1].strip().lower()
            assert stack and stack[-1] == name, f"unbalanced close {name}"
            stack.pop()
        elif tag.endswith("/>") or tag[1:-1].strip().lower().split()[0].rstrip("/") in void:
            continue
        else:
            name = tag[1:-1].strip().lower().split()[0]
            stack.append(name)
    assert not stack, f"unclosed tags: {stack}"


# ---------------------------------------------------------------------------
# _format_release_notification (pure)
# ---------------------------------------------------------------------------


def test_format_release_notification_happy():
    release = make_release(rid=1, tag="v1.0", name="Release", body="Bug fixes")
    expected = (
        "🚀 Новый релиз <b>owner/repo</b>\n\n"
        "<a href='https://github.com/o/r/releases/tag/v1.0'>v1.0</a> — Release\n\n"
        f"{html.escape('Bug fixes')}"
    )
    assert _format_release_notification("owner/repo", release) == expected


def test_format_release_notification_name_equals_tag():
    release = make_release(rid=1, tag="v1.0", name="v1.0", body="Bug fixes")
    expected = (
        "🚀 Новый релиз <b>owner/repo</b>\n\n"
        "<a href='https://github.com/o/r/releases/tag/v1.0'>v1.0</a>\n\n"
        f"{html.escape('Bug fixes')}"
    )
    assert _format_release_notification("owner/repo", release) == expected


@pytest.mark.parametrize("body_len", [500, 5000])
def test_format_release_notification_body_not_truncated(body_len):
    release = make_release(rid=1, tag="v1.0", name="Release", body="X" * body_len)
    result = _format_release_notification("owner/repo", release)
    assert "X" * body_len in result


def test_format_release_notification_empty_body():
    release = make_release(rid=1, tag="v1.0", name="Release", body="")
    expected = (
        "🚀 Новый релиз <b>owner/repo</b>\n\n<a href='https://github.com/o/r/releases/tag/v1.0'>v1.0</a> — Release"
    )
    assert _format_release_notification("owner/repo", release) == expected


def test_format_release_notification_with_rendered_body():
    release = make_release(rid=1, tag="v1.0", name="Release", body="<b>raw</b>")
    rendered = _github_html_to_telegram("<div><b>Hello</b></div><img src='x.png'>")
    result = _format_release_notification("owner/repo", release, rendered_body=rendered)
    assert "<b>Hello</b>" in result
    assert "<div" not in result
    assert "🚀 Новый релиз <b>owner/repo</b>" in result


def test_format_release_notification_body_html_fallback():
    release = make_release(rid=1, tag="v1.0", name="Release", body="<script>alert(1)</script>")
    result = _format_release_notification("owner/repo", release)
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


# ---------------------------------------------------------------------------
# _github_html_to_telegram (parametrized conversions)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "html_in, expected",
    [
        ("<b>bold</b>", "<b>bold</b>"),
        ("<strong>x</strong>", "<b>x</b>"),
        ("<i>ital</i>", "<i>ital</i>"),
        ("<em>e</em>", "<i>e</i>"),
        ("<u>u</u>", "<u>u</u>"),
        ("<s>s</s>", "<s>s</s>"),
        ("<strike>s</strike>", "<s>s</s>"),
        ("<del>d</del>", "<s>d</s>"),
        ("<code>c</code>", "<code>c</code>"),
        ("<pre><code>x</code></pre>", "<pre>x</pre>"),
        ("<blockquote>q</blockquote>", "<blockquote>q</blockquote>"),
        ('<a href="https://x.com" rel="nofollow">link</a>', '<a href="https://x.com">link</a>'),
        ("<h1>Title</h1>", "<b>Title</b>"),
        # <li> renders as a bullet; no </li> is emitted by handle_endtag.
        ("<li>item</li>", "• item"),
        ("<li>a</li><li>b</li>", "• a\n• b"),
        ('<img src="U" alt="X">', '<a href="U">🖼 X</a>'),
        ("<div>Hello</div>", "Hello"),
        ("<span>x</span>", "x"),
        ("&amp;", "&amp;"),
        ("<b>Tom &amp; Jerry</b>", "<b>Tom &amp; Jerry</b>"),
    ],
)
def test_github_html_to_telegram_parametrized(html_in, expected):
    assert _github_html_to_telegram(html_in) == expected


def test_github_html_to_telegram_br_to_newline():
    assert _github_html_to_telegram("line1<br>line2<br/>line3") == "line1\nline2\nline3"
    assert "<br" not in _github_html_to_telegram("a<br>b")


def test_github_html_to_telegram_collapses_newlines():
    # three source newlines must collapse to two; they must never remain as three.
    out = _github_html_to_telegram("x<br><br><br>y")
    assert out == "x\n\ny"
    assert "\n\n\n" not in out


def test_github_html_to_telegram_img_in_anchor_no_nested_anchor():
    assert _github_html_to_telegram('<a href="U"><img src="U" alt="Logo"></a>') == '<a href="U">🖼 Logo</a>'


# ---------------------------------------------------------------------------
# _split_html_safe (boundary behaviour)
# ---------------------------------------------------------------------------


def test_split_html_safe_within_limit():
    assert _split_html_safe("<b>hello</b>", limit=100) == ["<b>hello</b>"]


def test_split_html_safe_exact_limit():
    assert _split_html_safe("x" * 20, limit=20) == ["x" * 20]


def test_split_html_safe_long_tagless():
    text = "y" * 50
    chunks = _split_html_safe(text, limit=20)
    assert len(chunks) > 1
    assert "".join(chunks) == text


def test_split_html_safe_carries_tags_with_anchor():
    text = '<a href="u">' + "y" * 50 + "</a>"
    chunks = _split_html_safe(text, limit=20)
    assert_balanced("".join(chunks))


def test_split_html_safe_carries_open_tags():
    text = "<b>first</b><i>second</i>"
    chunks = _split_html_safe(text, limit=12)
    joined = "".join(chunks)
    assert joined == text
    for chunk in chunks:
        assert chunk.count("<b>") == chunk.count("</b>")
        assert chunk.count("<i>") == chunk.count("</i>")


# ---------------------------------------------------------------------------
# _render_release_body (caching + fallback)
# ---------------------------------------------------------------------------


async def test_render_release_body_empty():
    RENDER_CACHE.clear()
    assert await _render_release_body(make_release(rid=9, body="")) == ""


async def test_render_release_body_caches(monkeypatch):
    RENDER_CACHE.clear()
    calls = []

    async def _render(body):
        calls.append(body)
        return "<b>hello</b>"

    monkeypatch.setattr(checker, "render_markdown", _render)
    rel = make_release(rid=5, body="hello")
    first = await _render_release_body(rel)
    second = await _render_release_body(rel)
    assert first == _github_html_to_telegram("<b>hello</b>")
    assert second == first
    assert len(calls) == 1  # second call must hit the cache


async def test_render_release_body_fallback(monkeypatch):
    RENDER_CACHE.clear()

    async def _render(body):
        raise RuntimeError("x")

    monkeypatch.setattr(checker, "render_markdown", _render)
    rel = make_release(rid=7, body="<b>raw</b>")
    result = await _render_release_body(rel)
    assert result == html.escape("<b>raw</b>")
    assert 7 not in RENDER_CACHE  # fallback is not cached


# ---------------------------------------------------------------------------
# _send_release_notification
# ---------------------------------------------------------------------------


async def test_send_release_notification_basic(monkeypatch, fake_bot):
    RENDER_CACHE.clear()

    async def _render(body):
        return "<b>rendered</b>"

    monkeypatch.setattr(checker, "render_markdown", _render)
    rel = make_release(rid=3, tag="v1.0", name="R", body="hi")
    await _send_release_notification(fake_bot, 42, "owner/repo", rel)
    assert len(fake_bot.sent) == 1
    chat_id, text, kwargs = fake_bot.sent[0]
    assert chat_id == 42
    assert kwargs["parse_mode"] == "HTML"
    assert kwargs["disable_web_page_preview"] is True
    assert "owner/repo" in text
    assert "rendered" in text
    assert "🚀" in text


async def test_send_release_notification_splits(monkeypatch, fake_bot):
    RENDER_CACHE.clear()

    async def _render(body):
        return "y" * 5000

    monkeypatch.setattr(checker, "render_markdown", _render)
    rel = make_release(rid=4, tag="v1", body="x" * 5000)
    await _send_release_notification(fake_bot, 1, "o/r", rel)
    assert len(fake_bot.sent) > 1


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

    async def _render(_body):
        return "<b>rendered</b>"

    monkeypatch.setattr(checker, "render_markdown", _render)

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
    captured, _admin, sent = await _run_cycle(monkeypatch, fake_bot, subs, latest, [])
    assert len(sent) == 1
    saved = captured["snapshot"]["users"][1][0]
    assert saved["last_release_id"] == 10


async def test_run_check_cycle_same_id_no_notify(monkeypatch, fake_bot):
    subs = {"users": {1: [_repo_entry("o/r", last_release_id=10)]}}
    latest = make_release(rid=10, tag="v1.0")
    captured, _admin, sent = await _run_cycle(monkeypatch, fake_bot, subs, latest, [])
    assert sent == []
    assert captured["snapshot"]["users"][1][0]["last_release_id"] == 10


async def test_run_check_cycle_prerelease_notified(monkeypatch, fake_bot):
    subs = {"users": {1: [_repo_entry("o/r", last_release_id=5, notify_prerelease=True)]}}
    latest = make_release(rid=10, tag="v1.0")
    recent = [
        make_release(rid=20, tag="v2.0-pre", name="v2.0-pre", prerelease=True),
        make_release(rid=10, tag="v1.0", name="v1.0", prerelease=False),
    ]
    captured, _admin, sent = await _run_cycle(monkeypatch, fake_bot, subs, latest, recent)
    assert len(sent) == 1
    assert "v2.0-pre" in sent[0][1]
    assert captured["snapshot"]["users"][1][0]["last_release_id"] == 20


async def test_run_check_cycle_prerelease_ignored(monkeypatch, fake_bot):
    subs = {"users": {1: [_repo_entry("o/r", last_release_id=5, notify_prerelease=False)]}}
    latest = make_release(rid=10, tag="v1.0")
    recent = [
        make_release(rid=20, tag="v2.0-pre", name="v2.0-pre", prerelease=True),
        make_release(rid=10, tag="v1.0", name="v1.0", prerelease=False),
    ]
    captured, _admin, sent = await _run_cycle(monkeypatch, fake_bot, subs, latest, recent)
    assert len(sent) == 1
    assert "v1.0" in sent[0][1]
    assert "v2.0-pre" not in sent[0][1]
    assert captured["snapshot"]["users"][1][0]["last_release_id"] == 10


async def test_run_check_cycle_fetch_failure_notifies_admin(monkeypatch, fake_bot):
    subs = {"users": {1: [_repo_entry("o/r", last_release_id=None)]}}
    _captured, admin, sent = await _run_cycle(monkeypatch, fake_bot, subs, None, [], fetch_raises=True)
    assert sent == []
    assert admin  # notify_admin was called
