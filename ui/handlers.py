"""Telegram message and callback handlers.

Implements commands, main menu actions, inline repo management,
and FSM dialogs for adding/editing repo URLs.
All user-facing strings are in Russian.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    Message,
    ReplyKeyboardRemove,
)
from aiogram.utils.markdown import hlink

from core.config import REPOS_PER_PAGE
from core.models import RepoEntry, load_subscriptions, save_subscriptions
from github.github_api import fetch_last_n_releases, fetch_latest_release, fetch_repo_info
from ui.keyboards import (
    cancel_edit_keyboard,
    check_list_keyboard,
    confirmation_keyboard,
    main_menu_keyboard,
    repo_detail_keyboard,
    repo_list_keyboard,
)

if TYPE_CHECKING:
    from aiogram.fsm.context import FSMContext

router = Router()
logger = logging.getLogger(__name__)


class AddRepoStates(StatesGroup):
    """FSM states for adding and editing a repository URL."""

    waiting_for_add_url = State()
    waiting_for_edit_url = State()


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _user_repos(uid: int) -> list[RepoEntry]:
    """Return the list of repos for a given user (empty list if missing)."""
    subs = load_subscriptions()
    return subs.get("users", {}).get(uid, [])


def _set_user_repos(uid: int, repos: list[RepoEntry]) -> None:
    """Replace the entire repo list for a user and persist."""
    subs = load_subscriptions()
    if "users" not in subs:
        subs["users"] = {}
    subs["users"][uid] = repos
    save_subscriptions(subs)


def _parse_owner_repo(url: str) -> tuple[str, str] | None:
    """Extract owner and repo from a GitHub URL. Returns None if invalid."""
    prefix = "https://github.com/"
    if not url.startswith(prefix):
        return None
    parts = url[len(prefix) :].strip("/").split("/")
    if len(parts) >= 2:
        return parts[0], parts[1]
    return None


def _format_last_checked(iso_string: str) -> str:
    """Convert ISO timestamp to readable string, or 'никогда' if empty."""
    if not iso_string:
        return "никогда"
    try:
        dt = datetime.strptime(iso_string, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.strftime("%Y.%m.%d %H:%M")
    except ValueError:
        return iso_string


async def _validate_and_add_repo(uid: int, url: str) -> str | None:
    """Validate the URL by fetching repo info and the latest release, then add it.

    Returns an error message string on failure, or None on success.
    Sets last_release_id immediately so that existing releases are not treated as new.
    """
    parsed = _parse_owner_repo(url)
    if not parsed:
        return "Неверный формат ссылки. Используйте https://github.com/владелец/репозиторий"
    owner, repo = parsed
    try:
        info = await fetch_repo_info(owner, repo)
    except Exception:
        return "Не удалось получить информацию о репозитории. Проверьте ссылку или повторите позже."
    full_name = info.get("full_name")
    if not full_name:
        return "Некорректный ответ API. Попробуйте ещё раз."
    repos = _user_repos(uid)
    if any(r["name"] == full_name for r in repos):
        return "Вы уже отслеживаете этот репозиторий."

    # Fetch latest release to set the initial last_release_id
    try:
        latest = await fetch_latest_release(owner, repo)
    except Exception:
        latest = None

    new_entry: RepoEntry = {
        "url": f"https://github.com/{full_name}",
        "name": full_name,
        "last_release_id": latest["id"] if latest else None,
        "cached_releases": [],
        "last_checked": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    repos.append(new_entry)
    _set_user_repos(uid, repos)
    return None


# ---------------------------------------------------------------------------
# Command: /start
# ---------------------------------------------------------------------------


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Send a welcome message and the main menu."""
    await message.answer(
        "Привет! Я бот для отслеживания релизов GitHub 🚀\nИспользуй меню ниже, чтобы управлять подписками.",
        reply_markup=main_menu_keyboard(),
    )


# ---------------------------------------------------------------------------
# Command: /help
# ---------------------------------------------------------------------------


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Provide help information about the bot."""
    await message.answer(
        "🤖 <b>GitHub Release Tracker</b> — бот для отслеживания релизов.\n\n"
        "📋 <b>Мои репозитории</b> — просмотр отслеживаемых репозиториев.\n"
        "➕ <b>Добавить репозиторий</b> — начать отслеживание по ссылке.\n"
        "/add &lt;ссылка&gt; — быстро добавить репозиторий по ссылке без диалога.\n"
        "/check — ручная проверка обновлений.\n"
        "/export — выгрузить список ваших репозиториев (ссылками).\n"
        "/help — эта справка.",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Command: /check
# ---------------------------------------------------------------------------


@router.message(Command("check"))
async def cmd_check(message: Message) -> None:
    """Show user's repos with last check times and check buttons (page 0)."""
    repos = _user_repos(message.from_user.id)
    if not repos:
        await message.answer("У вас нет отслеживаемых репозиториев. Добавьте через ➕.")
        return

    page = 0
    per_page = REPOS_PER_PAGE
    start_idx = page * per_page
    end_idx = start_idx + per_page
    page_repos = repos[start_idx:end_idx]

    text_lines = ["<b>Ручная проверка:</b>"]
    for repo in page_repos:
        last_str = _format_last_checked(repo.get("last_checked", ""))
        text_lines.append(f"• {repo['name']} — {last_str}")
    text = "\n".join(text_lines)

    await message.answer(
        text,
        reply_markup=check_list_keyboard(repos, page=page, per_page=per_page),
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Command: /export
# ---------------------------------------------------------------------------


@router.message(Command("export"))
async def cmd_export(message: Message) -> None:
    """Send the user's tracked repos as a newline-separated list of URLs."""
    repos = _user_repos(message.from_user.id)
    if not repos:
        await message.answer("У вас нет отслеживаемых репозиториев.")
        return
    text = "\n".join(repo["url"] for repo in repos)
    await message.answer(text)


# ---------------------------------------------------------------------------
# Command: /add <url>
# ---------------------------------------------------------------------------


@router.message(Command("add"))
async def cmd_add(message: Message) -> None:
    """Add a GitHub repo directly by URL without the FSM dialog."""
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Отправьте ссылку: /add https://github.com/владелец/репозиторий")
        return
    url = parts[1].strip()
    error = await _validate_and_add_repo(message.from_user.id, url)
    if error:
        await message.answer(error)
    else:
        await message.answer("✅ Репозиторий успешно добавлен!")


# ---------------------------------------------------------------------------
# check_single callback – manually refresh one repo
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("check_single:"))
async def handle_check_single(callback: CallbackQuery) -> None:
    """Fetch latest releases for a specific repo and show detail."""
    index_str = callback.data.split(":", 1)[1]
    index = int(index_str)
    uid = callback.from_user.id
    repos = _user_repos(uid)
    if index < 0 or index >= len(repos):
        await callback.answer("Неверный индекс репозитория.", show_alert=True)
        return
    repo = repos[index]
    owner, repo_name = repo["name"].split("/", 1)
    await callback.answer("Проверяю обновления…")
    logger.info("User %d manually checking repository %s", uid, repo["name"])
    await callback.message.bot.send_chat_action(callback.message.chat.id, action="typing")
    try:
        latest = await fetch_latest_release(owner, repo_name)
        recent = await fetch_last_n_releases(owner, repo_name, 3)
    except Exception:
        await callback.message.edit_text("❌ Не удалось проверить релизы. Попробуйте позже.")
        return

    # Log whether a new release was found (before updating last_release_id)
    if latest:
        old_id = repo.get("last_release_id")
        if old_id is None or latest["id"] > old_id:
            logger.info("Manual check: new release found for %s (id=%d)", repo["name"], latest["id"])
        else:
            logger.info("Manual check: no new release for %s", repo["name"])

    # Update repo data
    repo["last_release_id"] = latest["id"] if latest else repo.get("last_release_id")
    repo["cached_releases"] = [
        {
            "tag_name": r["tag_name"],
            "name": r["name"],
            "html_url": r["html_url"],
            "published_at": r["published_at"],
        }
        for r in recent
    ]
    repo["last_checked"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _set_user_repos(uid, repos)

    # Show the updated detail
    await show_repo_detail(callback, index)


# ---------------------------------------------------------------------------
# Main menu text buttons (persistent keyboard)
# ---------------------------------------------------------------------------


@router.message(F.text == "📋 Мои репозитории")
async def show_my_repos(message: Message) -> None:
    """Display the list of tracked repositories with pagination."""
    repos = _user_repos(message.from_user.id)
    if not repos:
        await message.answer("Вы пока не отслеживаете ни одного репозитория. Нажмите ➕ Добавить, чтобы начать.")
        return
    await message.answer(
        "Ваши отслеживаемые репозитории:",
        reply_markup=repo_list_keyboard(repos, page=0, per_page=REPOS_PER_PAGE),
    )


@router.message(F.text == "➕ Добавить репозиторий")
async def prompt_add_repo(message: Message, state: FSMContext) -> None:
    """Enter the FSM state to capture the new repo URL."""
    await state.set_state(AddRepoStates.waiting_for_add_url)
    await message.answer(
        "Отправьте ссылку на GitHub-репозиторий (например https://github.com/psf/requests).",
        reply_markup=ReplyKeyboardRemove(),
    )


# ---------------------------------------------------------------------------
# FSM handler: receiving URL for addition
# ---------------------------------------------------------------------------


@router.message(AddRepoStates.waiting_for_add_url)
async def process_add_url(message: Message, state: FSMContext) -> None:
    """Validate the URL, add the repo, and return to the main menu."""
    url = message.text.strip()
    await message.bot.send_chat_action(message.chat.id, action="typing")
    error = await _validate_and_add_repo(message.from_user.id, url)
    if error:
        await message.answer(error)
        return  # keep the state for another try
    # Extract full_name from the validated URL for logging
    parsed = _parse_owner_repo(url)
    full_name = f"{parsed[0]}/{parsed[1]}" if parsed else url
    logger.info("User %d added repository: %s", message.from_user.id, full_name)
    await state.clear()
    await message.answer("✅ Репозиторий успешно добавлен!", reply_markup=main_menu_keyboard())


# ---------------------------------------------------------------------------
# Inline callback routing
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("repo:"))
async def handle_repo_action(callback: CallbackQuery, state: FSMContext) -> None:
    """Route repo inline actions (view, delete, edit)."""
    # Callback format: repo:<action>:<index>
    _, action, index_str = callback.data.split(":", 2)
    index = int(index_str)
    uid = callback.from_user.id
    repos = _user_repos(uid)
    if index < 0 or index >= len(repos):
        await callback.answer("Неверный индекс репозитория.", show_alert=True)
        return

    if action == "view":
        await show_repo_detail(callback, index)
    elif action == "delete":
        await show_delete_confirmation(callback, index)
    elif action == "edit":
        await start_edit_url(callback, state, index)
    else:
        await callback.answer("Неизвестное действие.", show_alert=True)


@router.callback_query(F.data.startswith("confirm_delete:"))
async def confirm_delete(callback: CallbackQuery) -> None:
    """Delete a repo after user confirmation."""
    index_str = callback.data.split(":", 1)[1]
    index = int(index_str)
    uid = callback.from_user.id
    repos = _user_repos(uid)
    if index < 0 or index >= len(repos):
        await callback.answer("Неверный репозиторий.", show_alert=True)
        return
    deleted_name = repos[index]["name"]
    del repos[index]
    _set_user_repos(uid, repos)
    logger.info("User %d deleted repository: %s", uid, deleted_name)
    await callback.message.edit_text(f"🗑 Репозиторий {deleted_name} удалён.")
    await callback.answer(f"{deleted_name} удалён.")


@router.callback_query(F.data == "cancel_delete")
async def cancel_delete(callback: CallbackQuery) -> None:
    """Cancel the deletion and show the repo list again (page 0)."""
    uid = callback.from_user.id
    repos = _user_repos(uid)
    await callback.message.edit_text(
        "Ваши отслеживаемые репозитории:",
        reply_markup=repo_list_keyboard(repos, page=0, per_page=REPOS_PER_PAGE),
    )
    await callback.answer("Удаление отменено.")


@router.callback_query(F.data == "cancel_edit")
async def cancel_edit(callback: CallbackQuery, state: FSMContext) -> None:
    """Cancel editing a repo URL and return to the repository list (page 0)."""
    await state.clear()
    uid = callback.from_user.id
    repos = _user_repos(uid)
    if not repos:
        await callback.message.edit_text("Вы пока не отслеживаете ни одного репозитория.")
        return
    await callback.message.edit_text(
        "Ваши отслеживаемые репозитории:",
        reply_markup=repo_list_keyboard(repos, page=0, per_page=REPOS_PER_PAGE),
    )
    await callback.answer("Редактирование отменено.")


# ---------------------------------------------------------------------------
# Delete flow
# ---------------------------------------------------------------------------


async def show_delete_confirmation(callback: CallbackQuery, index: int) -> None:
    """Prompt user to confirm deletion of a specific repo."""
    repos = _user_repos(callback.from_user.id)
    name = repos[index]["name"]
    await callback.message.edit_text(
        f"Вы уверены, что хотите удалить {name}?",
        reply_markup=confirmation_keyboard(index),
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Edit flow (change URL)
# ---------------------------------------------------------------------------


async def start_edit_url(callback: CallbackQuery, state: FSMContext, index: int) -> None:
    """Enter FSM state to edit a repository URL, with a cancel button."""
    await state.set_state(AddRepoStates.waiting_for_edit_url)
    # Store the index in FSM data
    await state.update_data(repo_index=index)
    repos = _user_repos(callback.from_user.id)
    name = repos[index]["name"]
    await callback.message.edit_text(
        f"Отправьте новую ссылку для <b>{name}</b>.\nТекущая ссылка: {repos[index]['url']}",
        parse_mode="HTML",
        reply_markup=cancel_edit_keyboard(),
    )
    await callback.answer()


@router.message(AddRepoStates.waiting_for_edit_url)
async def process_edit_url(message: Message, state: FSMContext) -> None:
    """Validate new URL, update the repo, and return to main menu."""
    uid = message.from_user.id
    data = await state.get_data()
    index = data.get("repo_index")
    if index is None:
        await message.answer("Что-то пошло не так. Попробуйте заново.")
        await state.clear()
        return

    url = message.text.strip()
    parsed = _parse_owner_repo(url)
    if not parsed:
        await message.answer("Неверная ссылка. Попробуйте ещё раз.")
        return
    owner, repo_name = parsed
    try:
        info = await fetch_repo_info(owner, repo_name)
        full_name = info.get("full_name")
    except Exception:
        await message.answer("Не удалось получить информацию о репозитории. Проверьте ссылку.")
        return
    if not full_name:
        await message.answer("Некорректный ответ API. Попробуйте ещё раз.")
        return

    repos = _user_repos(uid)
    if index >= len(repos):
        await message.answer("Репозиторий, похоже, был удалён.")
        await state.clear()
        return

    # Ensure no duplicate (excluding the one being edited)
    if any(i != index and r["name"] == full_name for i, r in enumerate(repos)):
        await message.answer("Вы уже отслеживаете этот репозиторий.")
        return

    old_name = repos[index]["name"]
    repos[index]["url"] = f"https://github.com/{full_name}"
    repos[index]["name"] = full_name
    repos[index]["last_release_id"] = None  # reset because URL changed
    repos[index]["last_checked"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _set_user_repos(uid, repos)
    logger.info("User %d updated repository URL: %s -> %s", uid, old_name, full_name)
    await state.clear()
    await message.answer("✅ Репозиторий успешно обновлён!", reply_markup=main_menu_keyboard())


# ---------------------------------------------------------------------------
# Releases action – show last 3 releases
# ---------------------------------------------------------------------------


# New function to display repo detail (replaces old show_recent_releases)
async def show_repo_detail(callback: CallbackQuery, index: int) -> None:
    """Display detailed view for a repository using cached release data (no API call).

    If cache is empty, falls back to fetching fresh data.
    """
    repos = _user_repos(callback.from_user.id)
    if index >= len(repos):
        await callback.answer("Репозиторий не найден.", show_alert=True)
        return
    repo = repos[index]
    cached = repo.get("cached_releases", [])

    # If cache is missing (e.g., just added and checker hasn't run yet), fetch once
    if not cached:
        owner, repo_name = repo["name"].split("/", 1)
        await callback.answer("Загружаю информацию…")
        try:
            releases = await fetch_last_n_releases(owner, repo_name, 3)
        except Exception:
            await callback.message.edit_text("❌ Не удалось загрузить релизы. Попробуйте позже.")
            return
        # Update the repo's cache immediately
        repo["cached_releases"] = [
            {
                "tag_name": r["tag_name"],
                "name": r["name"],
                "html_url": r["html_url"],
                "published_at": r["published_at"],
            }
            for r in releases
        ]
        _set_user_repos(callback.from_user.id, repos)  # persist the update
    else:
        releases = cached  # use the cached list directly
        await callback.answer()

    # Build message text
    text = f"<b>{repo['name']}</b>\n{repo['url']}\n\n"
    if releases:
        text += "<b>Последние релизы:</b>\n"
        for rel in releases:
            date_str = ""
            if rel.get("published_at"):
                try:
                    dt = datetime.strptime(rel["published_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                    date_str = dt.strftime("%Y.%m.%d")
                except ValueError:
                    date_str = rel["published_at"]
            text += f"• {hlink(rel['tag_name'], rel['html_url'])}"
            if rel.get("name") != rel["tag_name"]:
                text += f" - {rel['name']}"
            if date_str:
                text += f" ({date_str})"
            text += "\n"
    else:
        text += "Релизы не найдены."

    last_line = _format_last_checked(repo.get("last_checked", ""))
    text += f"\n<i>Последняя проверка: {last_line}</i>"

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=repo_detail_keyboard(index),
    )


# Handler for "back to list" button
@router.callback_query(F.data == "repo_list_back")
async def back_to_repo_list(callback: CallbackQuery) -> None:
    """Return to the repository list message (page 0)."""
    uid = callback.from_user.id
    repos = _user_repos(uid)
    if not repos:
        await callback.message.edit_text("Вы пока не отслеживаете ни одного репозитория.")
        return
    await callback.message.edit_text(
        "Ваши отслеживаемые репозитории:",
        reply_markup=repo_list_keyboard(repos, page=0, per_page=REPOS_PER_PAGE),
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Pagination handlers
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("repo_page:prev:"))
async def repo_page_prev(callback: CallbackQuery) -> None:
    """Go to the previous page of the repo list."""
    page_str = callback.data.split(":")[2]
    page = int(page_str)
    uid = callback.from_user.id
    repos = _user_repos(uid)
    new_page = max(0, page - 1)
    await callback.message.edit_text(
        "Ваши отслеживаемые репозитории:",
        reply_markup=repo_list_keyboard(repos, page=new_page, per_page=REPOS_PER_PAGE),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("repo_page:next:"))
async def repo_page_next(callback: CallbackQuery) -> None:
    """Go to the next page of the repo list."""
    page_str = callback.data.split(":")[2]
    page = int(page_str)
    uid = callback.from_user.id
    repos = _user_repos(uid)
    total_pages = (len(repos) + REPOS_PER_PAGE - 1) // REPOS_PER_PAGE
    new_page = min(total_pages - 1, page + 1)
    await callback.message.edit_text(
        "Ваши отслеживаемые репозитории:",
        reply_markup=repo_list_keyboard(repos, page=new_page, per_page=REPOS_PER_PAGE),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("check_page:prev:"))
async def check_page_prev(callback: CallbackQuery) -> None:
    """Go to the previous page of the /check list."""
    page_str = callback.data.split(":")[2]
    page = int(page_str)
    uid = callback.from_user.id
    repos = _user_repos(uid)
    per_page = REPOS_PER_PAGE
    new_page = max(0, page - 1)

    start_idx = new_page * per_page
    end_idx = start_idx + per_page
    page_repos = repos[start_idx:end_idx]

    text_lines = ["<b>Ручная проверка:</b>"]
    for repo in page_repos:
        last_str = _format_last_checked(repo.get("last_checked", ""))
        text_lines.append(f"• {repo['name']} — {last_str}")
    text = "\n".join(text_lines)

    await callback.message.edit_text(
        text,
        reply_markup=check_list_keyboard(repos, page=new_page, per_page=per_page),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("check_page:next:"))
async def check_page_next(callback: CallbackQuery) -> None:
    """Go to the next page of the /check list."""
    page_str = callback.data.split(":")[2]
    page = int(page_str)
    uid = callback.from_user.id
    repos = _user_repos(uid)
    per_page = REPOS_PER_PAGE
    total_pages = (len(repos) + per_page - 1) // per_page
    new_page = min(total_pages - 1, page + 1)

    start_idx = new_page * per_page
    end_idx = start_idx + per_page
    page_repos = repos[start_idx:end_idx]

    text_lines = ["<b>Ручная проверка:</b>"]
    for repo in page_repos:
        last_str = _format_last_checked(repo.get("last_checked", ""))
        text_lines.append(f"• {repo['name']} — {last_str}")
    text = "\n".join(text_lines)

    await callback.message.edit_text(
        text,
        reply_markup=check_list_keyboard(repos, page=new_page, per_page=per_page),
        parse_mode="HTML",
    )
    await callback.answer()
