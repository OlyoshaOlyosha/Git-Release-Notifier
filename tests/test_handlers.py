"""Handler-logic tests for ui.handlers (external I/O mocked)."""

import pytest
from unittest.mock import AsyncMock

import ui.handlers as handlers
from core.models import load_subscriptions, save_subscriptions
from conftest import FakeCallback, FakeMessage, make_release


def _repo(name, notify_prerelease=False, cached_releases=None, last_release_id=None):
    return {
        "url": f"https://github.com/{name}",
        "name": name,
        "last_release_id": last_release_id,
        "cached_releases": cached_releases or [],
        "last_checked": "",
        "notify_prerelease": notify_prerelease,
    }


def _seed(tmp_subs, uid, repos):
    save_subscriptions({"users": {uid: repos}})


@pytest.fixture
def mock_github(monkeypatch):
    async def _info(o, r):
        return {"full_name": f"{o}/{r}"}

    async def _latest(o, r):
        return make_release(rid=1, tag="v1.0")

    async def _recent(o, r, n=3, include_prerelease=False):
        return [make_release(rid=1, tag="v1.0")]

    async def _by_tag(o, r, tag):
        return make_release(rid=1, tag=tag)

    monkeypatch.setattr(handlers, "fetch_repo_info", _info)
    monkeypatch.setattr(handlers, "fetch_latest_release", _latest)
    monkeypatch.setattr(handlers, "fetch_last_n_releases", _recent)
    monkeypatch.setattr(handlers, "fetch_release_by_tag", _by_tag)


# ---------------------------------------------------------------------------
# Simple command handlers
# ---------------------------------------------------------------------------


async def test_cmd_start(fake_bot):
    msg = FakeMessage(uid=1, text="/start")
    await handlers.cmd_start(msg)
    assert msg.answer.await_count == 1
    assert "Привет" in msg.answer.call_args[0][0]


async def test_cmd_help(fake_bot):
    msg = FakeMessage(uid=1, text="/help")
    await handlers.cmd_help(msg)
    assert msg.answer.await_count == 1
    assert "GitHub Release Tracker" in msg.answer.call_args[0][0]


async def test_cmd_check_empty(tmp_subs, fake_bot):
    _seed(tmp_subs, 7, [])
    msg = FakeMessage(uid=7, text="/check")
    await handlers.cmd_check(msg)
    assert "нет отслеживаемых" in msg.answer.call_args[0][0]


async def test_cmd_check_with_repos(tmp_subs, fake_bot):
    _seed(tmp_subs, 7, [_repo("o/r")])
    msg = FakeMessage(uid=7, text="/check")
    await handlers.cmd_check(msg)
    text = msg.answer.call_args[0][0]
    assert "Ручная проверка" in text
    assert "o/r" in text


async def test_cmd_export_empty(tmp_subs, fake_bot):
    _seed(tmp_subs, 7, [])
    msg = FakeMessage(uid=7, text="/export")
    await handlers.cmd_export(msg)
    assert "нет отслеживаемых" in msg.answer.call_args[0][0]


async def test_cmd_export_lists(tmp_subs, fake_bot):
    _seed(tmp_subs, 7, [_repo("o/r"), _repo("a/b")])
    msg = FakeMessage(uid=7, text="/export")
    await handlers.cmd_export(msg)
    text = msg.answer.call_args[0][0]
    assert "https://github.com/o/r" in text
    assert "https://github.com/a/b" in text


async def test_cmd_add_no_url(fake_bot):
    msg = FakeMessage(uid=5, text="/add")
    await handlers.cmd_add(msg)
    assert "Отправьте ссылку" in msg.answer.call_args[0][0]


async def test_cmd_add(mock_github, fake_bot, monkeypatch):
    captured = {}

    async def _atomic(mutator):
        captured["mutator"] = mutator
        snap = {"users": {}}
        mutator(snap)
        captured["snap"] = snap

    monkeypatch.setattr(handlers, "atomic_update", _atomic)
    msg = FakeMessage(uid=5, text="/add https://github.com/psf/requests")
    await handlers.cmd_add(msg)
    snap = captured["snap"]
    assert 5 in snap["users"]
    repo = snap["users"][5][0]
    assert repo["name"] == "psf/requests"
    assert repo["url"] == "https://github.com/psf/requests"
    assert repo["last_release_id"] == 1
    msg.answer.assert_awaited()


async def test_cmd_add_duplicate(mock_github, fake_bot, monkeypatch, tmp_subs):
    _seed(tmp_subs, 5, [_repo("psf/requests")])
    msg = FakeMessage(uid=5, text="/add https://github.com/psf/requests")
    await handlers.cmd_add(msg)
    assert "уже отслеживаете" in msg.answer.call_args[0][0]


# ---------------------------------------------------------------------------
# Pre-release toggle
# ---------------------------------------------------------------------------


async def test_toggle_prerelease(tmp_subs, fake_bot):
    _seed(tmp_subs, 7, [_repo("o/r", notify_prerelease=False, cached_releases=[make_release(rid=1, tag="v1.0")])])
    cb = FakeCallback(data="toggle_prerelease:0", uid=7, bot=fake_bot)
    await handlers.handle_toggle_prerelease(cb)
    subs = load_subscriptions()
    assert subs["users"][7][0]["notify_prerelease"] is True
    cb.answer.assert_awaited()


async def test_toggle_prerelease_invalid_index(tmp_subs, fake_bot):
    _seed(tmp_subs, 7, [_repo("o/r")])
    cb = FakeCallback(data="toggle_prerelease:5", uid=7, bot=fake_bot)
    await handlers.handle_toggle_prerelease(cb)
    assert cb.answer.call_args.kwargs.get("show_alert") is True


# ---------------------------------------------------------------------------
# Release body fetch + notification
# ---------------------------------------------------------------------------


async def test_handle_release_body(tmp_subs, fake_bot, monkeypatch):
    captured = {}

    async def _send(bot, uid, name, release, header="📄 Релиз"):
        captured["args"] = (bot, uid, name, release, header)

    async def _by_tag(o, r, tag):
        return make_release(rid=99, tag=tag)

    monkeypatch.setattr(handlers, "_send_release_notification", _send)
    monkeypatch.setattr(handlers, "fetch_release_by_tag", _by_tag)
    _seed(tmp_subs, 7, [_repo("o/r", cached_releases=[make_release(rid=1, tag="v1.0")])])
    cb = FakeCallback(data="release_body:0:0", uid=7, bot=fake_bot)
    await handlers.handle_release_body(cb)
    assert captured["args"][1] == 7
    assert captured["args"][2] == "o/r"
    assert captured["args"][3]["id"] == 99
    cb.answer.assert_awaited()


# ---------------------------------------------------------------------------
# Menu / FSM / inline routing
# ---------------------------------------------------------------------------


async def test_show_my_repos_empty(tmp_subs, fake_bot):
    _seed(tmp_subs, 7, [])
    msg = FakeMessage(uid=7)
    await handlers.show_my_repos(msg)
    assert "не отслеживаете" in msg.answer.call_args[0][0]


async def test_show_my_repos_with(tmp_subs, fake_bot):
    _seed(tmp_subs, 7, [_repo("o/r", cached_releases=[make_release(rid=1, tag="v1.0")])])
    msg = FakeMessage(uid=7)
    await handlers.show_my_repos(msg)
    markup = msg.answer.call_args.kwargs["reply_markup"]
    texts = [btn.text for row in markup.inline_keyboard for btn in row]
    assert "o/r" in texts


async def test_prompt_add_repo(fake_bot):
    msg = FakeMessage(uid=7)
    state = AsyncMock()
    await handlers.prompt_add_repo(msg, state)
    state.set_state.assert_awaited()
    msg.answer.assert_awaited()


async def test_process_add_url(mock_github, fake_bot, monkeypatch):
    async def _atomic(mutator):
        pass

    monkeypatch.setattr(handlers, "atomic_update", _atomic)
    msg = FakeMessage(uid=7, text="https://github.com/psf/requests", bot=fake_bot)
    state = AsyncMock()
    await handlers.process_add_url(msg, state)
    state.clear.assert_awaited()
    assert msg.answer.call_args[0][0] == "✅ Репозиторий успешно добавлен!"


async def test_process_add_url_invalid(mock_github, fake_bot):
    msg = FakeMessage(uid=7, text="not a url", bot=fake_bot)
    state = AsyncMock()
    await handlers.process_add_url(msg, state)
    assert "Неверный формат" in msg.answer.call_args[0][0]


async def test_handle_repo_action_view(tmp_subs, fake_bot):
    _seed(tmp_subs, 7, [_repo("o/r", cached_releases=[make_release(rid=1, tag="v1.0")])])
    cb = FakeCallback(data="repo:view:0", uid=7, bot=fake_bot)
    state = AsyncMock()
    await handlers.handle_repo_action(cb, state)
    cb.message.edit_text.assert_awaited()


async def test_handle_repo_action_delete(tmp_subs, fake_bot):
    _seed(tmp_subs, 7, [_repo("o/r")])
    cb = FakeCallback(data="repo:delete:0", uid=7, bot=fake_bot)
    state = AsyncMock()
    await handlers.handle_repo_action(cb, state)
    cb.message.edit_text.assert_awaited()


async def test_handle_repo_action_edit(tmp_subs, fake_bot):
    _seed(tmp_subs, 7, [_repo("o/r")])
    cb = FakeCallback(data="repo:edit:0", uid=7, bot=fake_bot)
    state = AsyncMock()
    await handlers.handle_repo_action(cb, state)
    cb.message.edit_text.assert_awaited()


async def test_handle_repo_action_unknown(tmp_subs, fake_bot):
    _seed(tmp_subs, 7, [_repo("o/r")])
    cb = FakeCallback(data="repo:bogus:0", uid=7, bot=fake_bot)
    state = AsyncMock()
    await handlers.handle_repo_action(cb, state)
    assert cb.answer.call_args.kwargs.get("show_alert") is True


# ---------------------------------------------------------------------------
# Delete / edit flows
# ---------------------------------------------------------------------------


async def test_confirm_delete(tmp_subs, fake_bot):
    _seed(tmp_subs, 7, [_repo("o/r"), _repo("a/b")])
    cb = FakeCallback(data="confirm_delete:0", uid=7, bot=fake_bot)
    await handlers.confirm_delete(cb)
    subs = load_subscriptions()
    assert "o/r" not in [r["name"] for r in subs["users"][7]]
    cb.message.edit_text.assert_awaited()
    cb.answer.assert_awaited()


async def test_confirm_delete_invalid_index(tmp_subs, fake_bot):
    _seed(tmp_subs, 7, [_repo("o/r")])
    cb = FakeCallback(data="confirm_delete:9", uid=7, bot=fake_bot)
    await handlers.confirm_delete(cb)
    assert cb.answer.call_args.kwargs.get("show_alert") is True


async def test_cancel_delete(tmp_subs, fake_bot):
    _seed(tmp_subs, 7, [_repo("o/r")])
    cb = FakeCallback(data="cancel_delete", uid=7, bot=fake_bot)
    await handlers.cancel_delete(cb)
    cb.message.edit_text.assert_awaited()
    cb.answer.assert_awaited()


async def test_cancel_edit(tmp_subs, fake_bot):
    _seed(tmp_subs, 7, [_repo("o/r")])
    cb = FakeCallback(data="cancel_edit", uid=7, bot=fake_bot)
    state = AsyncMock()
    await handlers.cancel_edit(cb, state)
    state.clear.assert_awaited()
    cb.message.edit_text.assert_awaited()


async def test_show_delete_confirmation(tmp_subs, fake_bot):
    _seed(tmp_subs, 7, [_repo("o/r")])
    cb = FakeCallback(data="repo:delete:0", uid=7, bot=fake_bot)
    await handlers.show_delete_confirmation(cb, 0)
    cb.message.edit_text.assert_awaited()
    cb.answer.assert_awaited()


async def test_start_edit_url(tmp_subs, fake_bot):
    _seed(tmp_subs, 7, [_repo("o/r")])
    cb = FakeCallback(data="repo:edit:0", uid=7, bot=fake_bot)
    state = AsyncMock()
    await handlers.start_edit_url(cb, state, 0)
    state.set_state.assert_awaited()
    state.update_data.assert_awaited()
    cb.message.edit_text.assert_awaited()


async def test_process_edit_url(tmp_subs, fake_bot, monkeypatch):
    async def _info(o, r):
        return {"full_name": f"{o}/{r}"}

    async def _latest(o, r):
        return make_release(rid=1, tag="v1.0")

    monkeypatch.setattr(handlers, "fetch_repo_info", _info)
    monkeypatch.setattr(handlers, "fetch_latest_release", _latest)
    _seed(tmp_subs, 7, [_repo("o/r")])
    msg = FakeMessage(uid=7, text="https://github.com/psf/requests", bot=fake_bot)
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={"repo_index": 0})
    await handlers.process_edit_url(msg, state)
    subs = load_subscriptions()
    assert subs["users"][7][0]["name"] == "psf/requests"
    msg.answer.assert_awaited()


async def test_process_edit_url_invalid_url(tmp_subs, fake_bot, monkeypatch):
    async def _info(o, r):
        return {"full_name": f"{o}/{r}"}

    monkeypatch.setattr(handlers, "fetch_repo_info", _info)
    _seed(tmp_subs, 7, [_repo("o/r")])
    msg = FakeMessage(uid=7, text="https://not-github.com/x/y", bot=fake_bot)
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={"repo_index": 0})
    await handlers.process_edit_url(msg, state)
    assert "Неверная ссылка" in msg.answer.call_args[0][0]


# ---------------------------------------------------------------------------
# Pagination + manual check
# ---------------------------------------------------------------------------


async def test_back_to_repo_list(tmp_subs, fake_bot):
    _seed(tmp_subs, 7, [_repo("o/r")])
    cb = FakeCallback(data="repo_list_back", uid=7, bot=fake_bot)
    await handlers.back_to_repo_list(cb)
    cb.message.edit_text.assert_awaited()


async def test_repo_page_next(tmp_subs, fake_bot):
    _seed(tmp_subs, 7, [_repo(f"o/repo{i}") for i in range(12)])
    cb = FakeCallback(data="repo_page:next:0", uid=7, bot=fake_bot)
    await handlers.repo_page_next(cb)
    cb.message.edit_text.assert_awaited()


async def test_repo_page_prev(tmp_subs, fake_bot):
    _seed(tmp_subs, 7, [_repo(f"o/repo{i}") for i in range(12)])
    cb = FakeCallback(data="repo_page:prev:1", uid=7, bot=fake_bot)
    await handlers.repo_page_prev(cb)
    cb.message.edit_text.assert_awaited()


async def test_check_page_next(tmp_subs, fake_bot):
    _seed(tmp_subs, 7, [_repo(f"o/repo{i}") for i in range(12)])
    cb = FakeCallback(data="check_page:next:0", uid=7, bot=fake_bot)
    await handlers.check_page_next(cb)
    cb.message.edit_text.assert_awaited()


async def test_check_page_prev(tmp_subs, fake_bot):
    _seed(tmp_subs, 7, [_repo(f"o/repo{i}") for i in range(12)])
    cb = FakeCallback(data="check_page:prev:1", uid=7, bot=fake_bot)
    await handlers.check_page_prev(cb)
    cb.message.edit_text.assert_awaited()


async def test_handle_check_single(tmp_subs, fake_bot, mock_github):
    _seed(tmp_subs, 7, [_repo("o/r", cached_releases=[])])
    cb = FakeCallback(data="check_single:0", uid=7, bot=fake_bot)
    await handlers.handle_check_single(cb)
    cb.message.edit_text.assert_awaited()
    cb.answer.assert_awaited()
