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
    """Return the persistent main menu with '📋 Мои репозитории' and '➕ Добавить репозиторий'."""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="📋 Мои репозитории"))
    builder.row(KeyboardButton(text="➕ Добавить репозиторий"))
    return builder.as_markup(resize_keyboard=True)


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


def repo_detail_keyboard(index: int) -> InlineKeyboardMarkup:
    """Inline keyboard for the repository detail view.

    Provides Edit, Delete, and Back buttons.
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✏️ Изменить", callback_data=f"repo:edit:{index}"),
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"repo:delete:{index}"),
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
