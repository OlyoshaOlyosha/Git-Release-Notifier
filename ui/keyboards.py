"""Keyboard builders for the Telegram bot. Texts are in Russian."""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from core.models import RepoEntry


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Return the persistent main menu with repo actions and settings."""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="📋 Мои репозитории"))
    builder.row(KeyboardButton(text="➕ Добавить репозиторий"))
    builder.row(KeyboardButton(text="⚙️ Настройки"))
    return builder.as_markup(resize_keyboard=True)


def release_translate_keyboard(rid: int, translated: bool, repo_index: int | None = None) -> InlineKeyboardMarkup:
    """Inline keyboard under a release message: translate/original toggle.

    ``translated`` reflects the currently displayed state; pressing the button
    switches to the opposite one. Callback format: ``reltr:<rid>:<0|1>`` where
    the last field is the current state.
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🌐 Показать оригинал" if translated else "🌐 Перевести",
            callback_data=f"reltr:{rid}:{1 if translated else 0}",
        )
    )
    if repo_index is not None:
        builder.row(InlineKeyboardButton(text="⬅️ К репозиторию", callback_data=f"repo:view:{repo_index}"))
    return builder.as_markup()


# Language presets offered in the settings menu (code, label).
LANGUAGE_PRESETS: list[tuple[str, str]] = [
    ("ru", "Русский"),
    ("en", "English"),
    ("de", "Deutsch"),
    ("es", "Español"),
    ("fr", "Français"),
]


def settings_keyboard(settings: dict) -> InlineKeyboardMarkup:
    """Inline keyboard for the settings menu: auto-translate toggle + language."""
    builder = InlineKeyboardBuilder()
    auto = settings.get("auto_translate", False)
    lang = settings.get("translate_lang", "ru")
    builder.row(
        InlineKeyboardButton(
            text=f"🌐 Автоперевод: {'ВКЛ' if auto else 'ВЫКЛ'}",
            callback_data="settings:autotrans",
        )
    )
    for i in range(0, len(LANGUAGE_PRESETS), 3):
        row = []
        for code, label in LANGUAGE_PRESETS[i : i + 3]:
            mark = "✓ " if code == lang else ""
            row.append(InlineKeyboardButton(text=f"{mark}{code}", callback_data=f"settings:lang:{code}"))
        builder.row(*row)
    builder.row(
        InlineKeyboardButton(text="Другой язык…", callback_data="settings:langcustom"),
        InlineKeyboardButton(text="⬅️ Меню", callback_data="settings:close"),
    )
    return builder.as_markup()


def settings_text(settings: dict) -> str:
    """Human-readable settings summary for the settings menu."""
    auto = settings.get("auto_translate", False)
    lang = settings.get("translate_lang", "ru")
    mode = (
        "включён — релизы приходят сразу переведёнными"
        if auto
        else ("выключен — под каждым релизом есть кнопка «🌐 Перевести»")
    )
    return (
        "<b>⚙️ Настройки</b>\n\n"
        f"<b>Перевод релизов</b>\n"
        f"Автоперевод: {'включён' if auto else 'выключен'} ({mode})\n"
        f"Язык перевода: <b>{lang}</b>"
    )


def repo_list_keyboard(
    repos: list[RepoEntry],
    page: int = 0,
    per_page: int = 10,
) -> InlineKeyboardMarkup:
    """Build a paginated inline keyboard of tracked repos.

    Each page shows at most `per_page` repos. Navigation buttons appear when
    there is more than one page.
    """
    start_idx = page * per_page
    end_idx = start_idx + per_page
    page_repos = repos[start_idx:end_idx]
    total_pages = (len(repos) + per_page - 1) // per_page

    builder = InlineKeyboardBuilder()

    for idx, repo in enumerate(page_repos, start=start_idx):
        name = repo.get("name", "неизвестный")
        builder.row(InlineKeyboardButton(text=name, callback_data=f"repo:view:{idx}"))

    # Add navigation row if needed
    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(text="◀️", callback_data=f"repo_page:prev:{page}"))
        nav_buttons.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="ignore"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton(text="▶️", callback_data=f"repo_page:next:{page}"))
        builder.row(*nav_buttons)

    return builder.as_markup()


def confirmation_keyboard(index: int) -> InlineKeyboardMarkup:
    """Return a confirmation keyboard for repo deletion."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete:{index}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_delete"),
    )
    return builder.as_markup()


def repo_detail_keyboard(
    index: int, notify_prerelease: bool = False, releases: list | None = None
) -> InlineKeyboardMarkup:
    """Inline keyboard for the repository detail view.

    Provides Edit, Delete, a pre-release notification toggle, and Back buttons.
    When `releases` is given, also adds a per-release button to view its body.
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✏️ Изменить", callback_data=f"repo:edit:{index}"),
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"repo:delete:{index}"),
    )
    for i, rel in enumerate(releases or []):
        builder.row(
            InlineKeyboardButton(
                text=f"📄 {rel.get('tag_name', '')}",
                callback_data=f"release_body:{index}:{i}",
            ),
        )
    builder.row(
        InlineKeyboardButton(
            text=f"🔔 Пре-релизы: {'ВКЛ' if notify_prerelease else 'ВЫКЛ'}",
            callback_data=f"toggle_prerelease:{index}",
        ),
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data="repo_list_back"),
    )
    return builder.as_markup()


def cancel_edit_keyboard() -> InlineKeyboardMarkup:
    """Return a simple inline keyboard with a cancel button for edit URL prompt."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⬅️ Отмена", callback_data="cancel_edit"))
    return builder.as_markup()


def check_list_keyboard(
    repos: list[RepoEntry],
    page: int = 0,
    per_page: int = 10,
) -> InlineKeyboardMarkup:
    """Paginated inline keyboard for /check: repo name and manual check button per repo."""
    start_idx = page * per_page
    end_idx = start_idx + per_page
    page_repos = repos[start_idx:end_idx]
    total_pages = (len(repos) + per_page - 1) // per_page

    builder = InlineKeyboardBuilder()

    for idx, repo in enumerate(page_repos, start=start_idx):
        name = repo.get("name", "неизвестный")
        builder.row(
            InlineKeyboardButton(text=name, callback_data=f"repo:view:{idx}"),
            InlineKeyboardButton(text="🔄", callback_data=f"check_single:{idx}"),
        )

    # Navigation row
    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(text="◀️", callback_data=f"check_page:prev:{page}"))
        nav_buttons.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="ignore"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton(text="▶️", callback_data=f"check_page:next:{page}"))
        builder.row(*nav_buttons)

    return builder.as_markup()
