"""Pure-function tests for release notification formatting in github.checker."""

import html

from github.checker import (
    _format_release_notification,
    _github_html_to_telegram,
    _split_html_safe,
)


def test_format_release_notification_happy():
    release = {
        "id": 1,
        "tag_name": "v1.0",
        "name": "Release",
        "html_url": "https://github.com/owner/repo/releases/tag/v1.0",
        "body": "Bug fixes",
        "published_at": "2024-01-15T10:30:00Z",
    }
    expected = (
        "🚀 Новый релиз <b>owner/repo</b>\n\n"
        "<a href='https://github.com/owner/repo/releases/tag/v1.0'>v1.0</a> — Release\n\n"
        f"{html.escape('Bug fixes')}"
    )
    assert _format_release_notification("owner/repo", release) == expected


def test_format_release_notification_name_equals_tag():
    release = {
        "id": 1,
        "tag_name": "v1.0",
        "name": "v1.0",
        "html_url": "https://github.com/owner/repo/releases/tag/v1.0",
        "body": "Bug fixes",
        "published_at": "",
    }
    expected = (
        "🚀 Новый релиз <b>owner/repo</b>\n\n"
        "<a href='https://github.com/owner/repo/releases/tag/v1.0'>v1.0</a>\n\n"
        f"{html.escape('Bug fixes')}"
    )
    assert _format_release_notification("owner/repo", release) == expected


def test_format_release_notification_escapes_html():
    release = {
        "id": 1,
        "tag_name": "v1.0",
        "name": "Release",
        "html_url": "https://github.com/owner/repo/releases/tag/v1.0",
        "body": "<script>alert(1)</script>",
        "published_at": "",
    }
    result = _format_release_notification("owner/repo", release)
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


def test_format_release_notification_body_not_truncated():
    body = "X" * 500
    release = {
        "id": 1,
        "tag_name": "v1.0",
        "name": "Release",
        "html_url": "https://github.com/owner/repo/releases/tag/v1.0",
        "body": body,
        "published_at": "",
    }
    result = _format_release_notification("owner/repo", release)
    assert "X" * 500 in result


def test_format_release_notification_body_long_not_truncated():
    body = "X" * 5000
    release = {
        "id": 1,
        "tag_name": "v1.0",
        "name": "Release",
        "html_url": "https://github.com/owner/repo/releases/tag/v1.0",
        "body": body,
        "published_at": "",
    }
    result = _format_release_notification("owner/repo", release)
    assert "X" * 5000 in result


def test_format_release_notification_empty_body():
    release = {
        "id": 1,
        "tag_name": "v1.0",
        "name": "Release",
        "html_url": "https://github.com/owner/repo/releases/tag/v1.0",
        "body": "",
        "published_at": "",
    }
    expected = (
        "🚀 Новый релиз <b>owner/repo</b>\n\n"
        "<a href='https://github.com/owner/repo/releases/tag/v1.0'>v1.0</a> — Release"
    )
    assert _format_release_notification("owner/repo", release) == expected


def test_github_html_to_telegram_br_to_newline():
    # Both <br> (starttag) and <br/> (startendtag) must become literal newlines.
    assert _github_html_to_telegram("line1<br>line2<br/>line3") == "line1\nline2\nline3"
    assert "<br" not in _github_html_to_telegram("a<br>b")


def test_github_html_to_telegram_heading_bold():
    result = _github_html_to_telegram("<h3>Upgrade</h3>")
    assert "<b>Upgrade</b>" in result
    assert "<h3" not in result


def test_github_html_to_telegram_div_dropped():
    result = _github_html_to_telegram("<div>Hello</div>")
    assert result == "Hello"


def test_github_html_to_telegram_img_dropped():
    result = _github_html_to_telegram('<p>Text <img src="x.png" alt="pic"> more</p>')
    assert "<img" not in result
    assert "Text" in result
    assert "more" in result


def test_github_html_to_telegram_pre_inner_code_clean():
    result = _github_html_to_telegram("<pre><code>print(1)</code></pre>")
    assert result == "<pre>print(1)</pre>"


def test_github_html_to_telegram_a_href_kept():
    result = _github_html_to_telegram('<a href="https://x.com" rel="nofollow">link</a>')
    assert result == '<a href="https://x.com">link</a>'


def test_format_release_notification_with_rendered_body():
    release = {
        "id": 1,
        "tag_name": "v1.0",
        "name": "Release",
        "html_url": "https://github.com/owner/repo/releases/tag/v1.0",
        "body": "<b>raw</b>",
        "body_html": "",
        "published_at": "",
    }
    rendered = _github_html_to_telegram("<div><b>Hello</b></div><img src='x.png'>")
    result = _format_release_notification("owner/repo", release, rendered_body=rendered)
    assert "<b>Hello</b>" in result
    assert "<div" not in result
    assert "<h3" not in result
    # header/link lines unchanged
    assert "🚀 Новый релиз <b>owner/repo</b>" in result


def test_format_release_notification_body_html_fallback():
    release = {
        "id": 1,
        "tag_name": "v1.0",
        "name": "Release",
        "html_url": "https://github.com/owner/repo/releases/tag/v1.0",
        "body": "<script>alert(1)</script>",
        "body_html": "",
        "published_at": "",
    }
    result = _format_release_notification("owner/repo", release)
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


def test_github_html_to_telegram_img_in_anchor_no_nested_anchor():
    result = _github_html_to_telegram('<a href="U"><img src="U" alt="Logo"></a>')
    assert result == '<a href="U">🖼 Logo</a>'


def test_github_html_to_telegram_standalone_img_to_link():
    result = _github_html_to_telegram('<img src="U" alt="X">')
    assert result == '<a href="U">🖼 X</a>'


def test_split_html_safe_carries_open_tags():
    text = "<b>first</b><i>second</i>"
    chunks = _split_html_safe(text, limit=12)
    # "first" alone is short; ensure splits keep tags valid and nothing is broken
    joined = "".join(chunks)
    assert joined == text
    for chunk in chunks:
        # every chunk must be well-formed: count of <b> equals </b>, etc.
        assert chunk.count("<b>") == chunk.count("</b>")
        assert chunk.count("<i>") == chunk.count("</i>")
