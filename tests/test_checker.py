"""Pure-function tests for release notification formatting in github.checker."""

import html

from github.checker import _format_release_notification


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
