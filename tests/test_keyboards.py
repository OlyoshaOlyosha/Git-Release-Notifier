"""Tests for inline/reply keyboard builders in ui.keyboards."""

from ui.keyboards import (
    cancel_edit_keyboard,
    check_list_keyboard,
    confirmation_keyboard,
    main_menu_keyboard,
    repo_detail_keyboard,
    repo_list_keyboard,
)


def _repo(name: str, notify_prerelease: bool = False) -> dict:
    return {
        "url": f"https://github.com/{name}",
        "name": name,
        "last_release_id": None,
        "cached_releases": [],
        "last_checked": "",
        "notify_prerelease": notify_prerelease,
    }


def _callback_datas(markup) -> list[str]:
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


def _button_texts(markup) -> list[str]:
    return [btn.text for row in markup.inline_keyboard for btn in row]


def test_main_menu_keyboard():
    kb = main_menu_keyboard()
    texts = [btn.text for row in kb.keyboard for btn in row]
    assert texts == ["📋 Мои репозитории", "➕ Добавить репозиторий"]


def test_repo_list_keyboard_buttons_and_callbacks():
    repos = [_repo(f"owner{i}/repo{i}") for i in range(3)]
    kb = repo_list_keyboard(repos, page=0, per_page=10)
    datas = _callback_datas(kb)
    assert datas == ["repo:view:0", "repo:view:1", "repo:view:2"]
    assert "repo_page:next:0" not in datas  # no pagination with 3 <= per_page


def test_repo_list_keyboard_pagination_present():
    repos = [_repo(f"owner{i}/repo{i}") for i in range(12)]
    kb = repo_list_keyboard(repos, page=0, per_page=10)
    datas = _callback_datas(kb)
    assert "repo_page:next:0" in datas
    assert "repo:view:9" in datas  # page 0 shows indices 0..9
    assert "repo:view:10" not in datas  # index 10 belongs to page 1


def test_repo_list_keyboard_second_page_prev():
    repos = [_repo(f"owner{i}/repo{i}") for i in range(12)]
    kb = repo_list_keyboard(repos, page=1, per_page=10)
    datas = _callback_datas(kb)
    assert "repo_page:prev:1" in datas
    assert "repo_page:next:1" not in datas


def test_confirmation_keyboard():
    kb = confirmation_keyboard(3)
    texts = _button_texts(kb)
    datas = _callback_datas(kb)
    assert texts[0] == "✅ Да, удалить"
    assert texts[1] == "❌ Отмена"
    assert datas[0] == "confirm_delete:3"
    assert datas[1] == "cancel_delete"


def test_repo_detail_keyboard_prerelease_off():
    releases = [{"tag_name": "v1.0"}]
    kb = repo_detail_keyboard(2, notify_prerelease=False, releases=releases)
    texts = _button_texts(kb)
    datas = _callback_datas(kb)
    assert "✏️ Изменить" in texts and "repo:edit:2" in datas
    assert "🗑 Удалить" in texts and "repo:delete:2" in datas
    assert "📄 v1.0" in texts and "release_body:2:0" in datas
    assert "🔔 Пре-релизы: ВЫКЛ" in texts
    assert "toggle_prerelease:2" in datas
    assert "repo_list_back" in datas


def test_repo_detail_keyboard_prerelease_on():
    kb = repo_detail_keyboard(0, notify_prerelease=True, releases=[])
    texts = _button_texts(kb)
    assert "🔔 Пре-релизы: ВКЛ" in texts


def test_cancel_edit_keyboard():
    kb = cancel_edit_keyboard()
    datas = _callback_datas(kb)
    assert datas == ["cancel_edit"]


def test_check_list_keyboard_buttons_and_callbacks():
    repos = [_repo(f"owner{i}/repo{i}") for i in range(3)]
    kb = check_list_keyboard(repos, page=0, per_page=10)
    datas = _callback_datas(kb)
    assert "repo:view:0" in datas
    assert "check_single:0" in datas
    assert "check_page:next:0" not in datas


def test_check_list_keyboard_pagination():
    repos = [_repo(f"owner{i}/repo{i}") for i in range(12)]
    kb = check_list_keyboard(repos, page=0, per_page=10)
    datas = _callback_datas(kb)
    assert "check_page:next:0" in datas
