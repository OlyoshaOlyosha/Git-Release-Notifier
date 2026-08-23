"""Pure-function tests for ReleaseInfo extraction in github.github_api."""

import pytest

from github.github_api import _extract_release_info


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
    # Required keys (id, tag_name, html_url) are accessed directly and raise on absence.
    with pytest.raises(KeyError):
        _extract_release_info({"tag_name": "v1.0", "html_url": "u"})
