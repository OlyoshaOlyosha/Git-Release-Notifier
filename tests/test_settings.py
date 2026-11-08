"""Tests for ui.settings: settings menu and release translate/original toggle."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import ui.settings as settings_mod
from conftest import FakeCallback, FakeMessage, make_release
from core.models import get_user_settings, load_subscriptions, save_subscriptions


def _seed(tmp_subs, uid, auto_translate=False, translate_lang="ru"):
    """Write a subscriptions file with (optionally) settings for the user."""
    subs = {"users": {uid: []}}
    if auto_translate or translate_lang != "ru":
        subs["settings"] = {uid: {"auto_translate": auto_translate, "translate_lang": translate_lang}}
    save_subscriptions(subs)
    return load_subscriptions()


class _MsgWithEdit:
    """Callback message stub with a recorded edit_text call."""

    def __init__(self, bot=None):
        self.bot = bot
        self.edit_text = AsyncMock()


# ---------------------------------------------------------------------------
# Settings menu
# ---------------------------------------------------------------------------


async def test_open_settings_defaults(tmp_subs):
    _seed(tmp_subs, 7)
    msg = FakeMessage(uid=7)
    msg.answer = AsyncMock()
    await settings_mod.open_settings(msg)
    text = msg.answer.await_args.args[0]
    assert "выключен" in text
    markup = msg.answer.await_args.kwargs["reply_markup"]
    datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "settings:autotrans" in datas


async def test_toggle_autotrans(tmp_subs):
    _seed(tmp_subs, 7)
    cb = FakeCallback(data="settings:autotrans", uid=7)
    cb.message = _MsgWithEdit()
    await settings_mod.toggle_autotrans(cb)
    assert get_user_settings(load_subscriptions(), 7)["auto_translate"] is True
    assert "включён" in cb.message.edit_text.await_args.args[0]


async def test_set_language_preset(tmp_subs):
    _seed(tmp_subs, 7)
    cb = FakeCallback(data="settings:lang:de", uid=7)
    cb.message = _MsgWithEdit()
    await settings_mod.set_language(cb)
    assert get_user_settings(load_subscriptions(), 7)["translate_lang"] == "de"


async def test_custom_language_valid(tmp_subs):
    _seed(tmp_subs, 7)
    msg = FakeMessage(uid=7, text=" DE ")
    msg.answer = AsyncMock()
    state = SimpleNamespace(set_state=AsyncMock(), clear=AsyncMock())
    await settings_mod.process_custom_language(msg, state)
    assert get_user_settings(load_subscriptions(), 7)["translate_lang"] == "de"
    state.clear.assert_awaited()


async def test_custom_language_invalid(tmp_subs):
    _seed(tmp_subs, 7)
    msg = FakeMessage(uid=7, text="not a lang!")
    msg.answer = AsyncMock()
    state = SimpleNamespace(set_state=AsyncMock(), clear=AsyncMock())
    await settings_mod.process_custom_language(msg, state)
    assert get_user_settings(load_subscriptions(), 7)["translate_lang"] == "ru"
    assert "Неверный" in msg.answer.await_args.args[0]
    state.clear.assert_not_awaited()


# ---------------------------------------------------------------------------
# reltr: release translate/original toggle
# ---------------------------------------------------------------------------


async def test_reltr_translates_and_reverts(monkeypatch, tmp_subs):
    _seed(tmp_subs, 7, auto_translate=False, translate_lang="ru")
    import github.checker as checker

    monkeypatch.setattr(checker, "to_rich", lambda b: f"<b>{b}</b>")
    checker.RELEASE_CACHE[5] = {
        "release": make_release(rid=5, tag="v1", name="R", body="hello"),
        "repo_name": "o/r",
        "header": "🚀 Новый релиз",
        "repo_index": None,
    }

    async def _fake_translate(text, lang):
        return {"hello": "привет", "R": "Р"}.get(text, text) if lang else text

    monkeypatch.setattr(checker, "translate_text", _fake_translate)
    msg = _MsgWithEdit()

    # Toggle original -> translated
    cb = FakeCallback(data="reltr:5:0", uid=7)
    cb.message = msg
    await settings_mod.toggle_release_translation(cb)
    out = msg.edit_text.await_args.kwargs["rich_message"].html
    assert "привет" in out
    assert msg.edit_text.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "reltr:5:1"

    # Toggle back -> original, no new translation call
    cb2 = FakeCallback(data="reltr:5:1", uid=7)
    cb2.message = msg
    await settings_mod.toggle_release_translation(cb2)
    out2 = msg.edit_text.await_args.kwargs["rich_message"].html
    assert "hello" in out2 and "привет" not in out2 and "Р" not in out2


async def test_reltr_unknown_release(tmp_subs):
    _seed(tmp_subs, 7)
    cb = FakeCallback(data="reltr:999:0", uid=7)
    cb.message = _MsgWithEdit()
    await settings_mod.toggle_release_translation(cb)
    cb.answer.assert_awaited_once_with(
        "Этот релиз уже не в памяти бота. Откройте его заново из списка репозитория.",
        show_alert=True,
    )
