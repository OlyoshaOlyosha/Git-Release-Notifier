"""Shared pytest fixtures and fake objects for the test suite."""

from __future__ import annotations

import types

import pytest

import core.models
import github.checker
from core.models import ReleaseInfo

from unittest.mock import AsyncMock


class FakeBot:
    """Minimal stand-in for an aiogram Bot used to capture outgoing messages."""

    def __init__(self) -> None:
        self.sent: list = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))
        return None

    async def send_chat_action(self, *args, **kwargs):
        return None

    async def answer(self, *args, **kwargs):
        return None


@pytest.fixture
def fake_bot():
    return FakeBot()


@pytest.fixture
def tmp_subs(tmp_path, monkeypatch):
    """Point core.models.SUBSCRIPTIONS_FILE at a temp file for the test."""
    path = tmp_path / "subscriptions.json"
    monkeypatch.setattr(core.models, "SUBSCRIPTIONS_FILE", str(path))
    return path


@pytest.fixture(autouse=True)
def _clear_render_cache():
    """Reset the render cache between tests to avoid cross-test hits."""
    github.checker.RENDER_CACHE.clear()
    yield
    github.checker.RENDER_CACHE.clear()


def make_release(
    rid=1,
    tag="v1.0",
    name=None,
    body="",
    prerelease=False,
    html_url="https://github.com/o/r/releases/tag/v1.0",
) -> ReleaseInfo:
    """Build a ReleaseInfo-shaped dict for tests."""
    return {
        "id": rid,
        "tag_name": tag,
        "name": name if name is not None else tag,
        "html_url": html_url,
        "body": body,
        "body_html": "",
        "published_at": "",
        "prerelease": prerelease,
    }


# ---------------------------------------------------------------------------
# Lightweight fakes for aiogram message / callback objects (handlers tests).
# ---------------------------------------------------------------------------


class FakeUser:
    def __init__(self, uid: int = 1) -> None:
        self.id = uid


class FakeMessage:
    def __init__(self, uid: int = 1, text: str = "", bot=None) -> None:
        self.from_user = FakeUser(uid)
        self.text = text
        self.bot = bot
        self.chat = types.SimpleNamespace(id=1)
        self.answer = AsyncMock()


class FakeCallbackMessage:
    def __init__(self, bot=None) -> None:
        self.bot = bot
        self.edit_text = AsyncMock()
        self.chat = types.SimpleNamespace(id=1)


class FakeCallback:
    def __init__(self, data: str = "", uid: int = 1, bot=None) -> None:
        self.data = data
        self.from_user = FakeUser(uid)
        self.message = FakeCallbackMessage(bot=bot)
        self.answer = AsyncMock()
