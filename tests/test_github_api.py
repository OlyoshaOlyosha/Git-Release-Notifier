"""Tests for the GitHub API network layer in github.github_api."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import github.github_api as gh
from github.github_api import (
    fetch_last_n_releases,
    fetch_latest_release,
    fetch_release_by_tag,
    fetch_repo_info,
    render_markdown,
)
from github.github_api import _extract_release_info


def _ctx(resp):
    ctx = AsyncMock()
    ctx.__aenter__.return_value = resp
    ctx.__aexit__.return_value = False
    return ctx


def _response(status=200, json_data=None, text="html"):
    resp = MagicMock()
    resp.status = status
    resp.raise_for_status = MagicMock()
    resp.json = AsyncMock(return_value=json_data if json_data is not None else {})
    resp.text = AsyncMock(return_value=text)
    return resp


def _session_with(get_resp=None, post_resp=None):
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = False
    session.get = MagicMock(return_value=_ctx(get_resp))
    session.post = MagicMock(return_value=_ctx(post_resp))
    return session


# ---------------------------------------------------------------------------
# Existing pure-function tests for _extract_release_info
# ---------------------------------------------------------------------------


def test_extract_release_info_happy():
    data = {
        "id": 1,
        "tag_name": "v1.0",
        "name": "Release 1.0",
        "html_url": "https://github.com/owner/repo/releases/tag/v1.0",
        "body": "Some notes",
        "published_at": "2024-01-15T10:30:00Z",
    }
    info = _extract_release_info(data)
    assert info == {
        "id": 1,
        "tag_name": "v1.0",
        "name": "Release 1.0",
        "html_url": "https://github.com/owner/repo/releases/tag/v1.0",
        "body": "Some notes",
        "body_html": "",
        "published_at": "2024-01-15T10:30:00Z",
    }


def test_extract_release_info_name_missing():
    data = {"id": 1, "tag_name": "v1.0", "html_url": "u", "body": None, "published_at": ""}
    assert _extract_release_info(data)["name"] == "v1.0"


def test_extract_release_info_name_empty():
    data = {"id": 1, "tag_name": "v1.0", "name": "", "html_url": "u", "body": "", "published_at": ""}
    assert _extract_release_info(data)["name"] == "v1.0"


def test_extract_release_info_body_missing():
    data = {"id": 1, "tag_name": "v1.0", "html_url": "u", "published_at": ""}
    assert _extract_release_info(data)["body"] == ""


def test_extract_release_info_body_none():
    data = {"id": 1, "tag_name": "v1.0", "html_url": "u", "body": None, "published_at": ""}
    assert _extract_release_info(data)["body"] == ""


def test_extract_release_info_published_at_missing():
    data = {"id": 1, "tag_name": "v1.0", "html_url": "u", "body": ""}
    assert _extract_release_info(data)["published_at"] == ""


def test_extract_release_info_missing_required_key():
    with pytest.raises(KeyError):
        _extract_release_info({"tag_name": "v1.0", "html_url": "u"})


# ---------------------------------------------------------------------------
# Network-layer tests (mock aiohttp.ClientSession)
# ---------------------------------------------------------------------------


async def test_fetch_latest_release_200():
    resp = _response(
        status=200,
        json_data={"id": 1, "tag_name": "v1", "html_url": "u", "body": "b", "published_at": "t"},
    )
    session = _session_with(get_resp=resp)
    with patch.object(gh.aiohttp, "ClientSession", return_value=session):
        result = await fetch_latest_release("o", "r")
    assert result["id"] == 1
    assert result["tag_name"] == "v1"


async def test_fetch_latest_release_404():
    session = _session_with(get_resp=_response(status=404))
    with patch.object(gh.aiohttp, "ClientSession", return_value=session):
        assert await fetch_latest_release("o", "r") is None


async def test_fetch_latest_release_500_raises():
    resp = _response(status=500)
    resp.raise_for_status = MagicMock(side_effect=RuntimeError("boom"))
    session = _session_with(get_resp=resp)
    with patch.object(gh.aiohttp, "ClientSession", return_value=session):
        with pytest.raises(RuntimeError):
            await fetch_latest_release("o", "r")


async def test_fetch_release_by_tag_200():
    resp = _response(
        status=200,
        json_data={"id": 2, "tag_name": "v2", "html_url": "u", "body": "", "published_at": ""},
    )
    session = _session_with(get_resp=resp)
    with patch.object(gh.aiohttp, "ClientSession", return_value=session):
        result = await fetch_release_by_tag("o", "r", "v2")
    assert result["id"] == 2


async def test_fetch_release_by_tag_404():
    session = _session_with(get_resp=_response(status=404))
    with patch.object(gh.aiohttp, "ClientSession", return_value=session):
        assert await fetch_release_by_tag("o", "r", "v2") is None


async def test_fetch_release_by_tag_500_raises():
    resp = _response(status=500)
    resp.raise_for_status = MagicMock(side_effect=RuntimeError("boom"))
    session = _session_with(get_resp=resp)
    with patch.object(gh.aiohttp, "ClientSession", return_value=session):
        with pytest.raises(RuntimeError):
            await fetch_release_by_tag("o", "r", "v2")


async def test_fetch_last_n_releases_filters():
    data = [
        {
            "id": 1,
            "tag_name": "v1",
            "html_url": "u1",
            "body": "",
            "published_at": "",
            "draft": True,
            "prerelease": False,
        },
        {
            "id": 2,
            "tag_name": "v2",
            "html_url": "u2",
            "body": "",
            "published_at": "",
            "draft": False,
            "prerelease": True,
        },
        {
            "id": 3,
            "tag_name": "v3",
            "html_url": "u3",
            "body": "",
            "published_at": "",
            "draft": False,
            "prerelease": False,
        },
    ]
    session = _session_with(get_resp=_response(status=200, json_data=data))
    with patch.object(gh.aiohttp, "ClientSession", return_value=session):
        out_false = await fetch_last_n_releases("o", "r", 3, include_prerelease=False)
        out_true = await fetch_last_n_releases("o", "r", 3, include_prerelease=True)

    get_url = session.get.call_args[0][0]
    assert "per_page=3" in get_url

    # include_prerelease=False -> draft + prerelease excluded -> only id 3
    assert [r["id"] for r in out_false] == [3]
    # include_prerelease=True -> prerelease kept, draft excluded -> ids 2, 3 (input order)
    assert [r["id"] for r in out_true] == [2, 3]
    assert [r["prerelease"] for r in out_true] == [True, False]


async def test_render_markdown_payload_and_auth_header(monkeypatch):
    captured = {}
    resp = _response(status=200, text="<html>RENDERED</html>")
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = False
    session.get = MagicMock()

    def _post(url, data=None, **kwargs):
        captured["url"] = url
        captured["data"] = data
        return _ctx(resp)

    session.post = MagicMock(side_effect=_post)

    def _make_session(*args, **kwargs):
        captured["headers"] = kwargs.get("headers", {})
        return session

    with patch.object(gh.aiohttp, "ClientSession", side_effect=_make_session):
        monkeypatch.setattr(gh, "GITHUB_TOKEN", "SECRET")
        text = await render_markdown("**hi**")

    assert captured["url"].endswith("/markdown")
    payload = json.loads(captured["data"])
    assert payload["text"] == "**hi**"
    assert payload["mode"] == "gfm"
    assert captured["headers"].get("Authorization") == "token SECRET"
    assert text == "<html>RENDERED</html>"


async def test_render_markdown_no_token_no_auth_header(monkeypatch):
    resp = _response(status=200, text="x")
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = False
    session.get = MagicMock()
    session.post = MagicMock(return_value=_ctx(resp))
    captured = {}

    def _make_session(*args, **kwargs):
        captured["headers"] = kwargs.get("headers", {})
        return session

    with patch.object(gh.aiohttp, "ClientSession", side_effect=_make_session):
        monkeypatch.setattr(gh, "GITHUB_TOKEN", "")
        await render_markdown("t")
    assert "Authorization" not in captured["headers"]


async def test_fetch_repo_info_200():
    session = _session_with(get_resp=_response(status=200, json_data={"full_name": "o/r"}))
    with patch.object(gh.aiohttp, "ClientSession", return_value=session):
        out = await fetch_repo_info("o", "r")
    assert out["full_name"] == "o/r"
