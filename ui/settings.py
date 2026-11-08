"""Settings menu and release-translation toggle handlers.

Owns the per-user translation settings (auto-translate on/off, target
language) and the inline button under release messages that switches a
release between the original and its translation. All user-facing strings
are in Russian.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InputRichMessage, Message

from core.models import atomic_update, get_user_settings, load_subscriptions, set_user_settings
from github.checker import RELEASE_CACHE, build_release_html
from ui.keyboards import release_translate_keyboard, settings_keyboard, settings_text
from ui.lang_codes import codes_overview, normalize_lang

router = Router()
logger = logging.getLogger(__name__)


class SettingsStates(StatesGroup):
    """FSM states for the settings dialog."""

    waiting_for_lang = State()


async def _show_settings(message: Message, settings: dict) -> None:
    """Render the settings menu as an edit on an existing message."""
    await message.edit_text(
        settings_text(settings),
        parse_mode="HTML",
        reply_markup=settings_keyboard(settings),
    )


@router.message(F.text == "⚙️ Настройки")
async def open_settings(message: Message) -> None:
    """Open the settings menu from the main keyboard."""
    uid = message.from_user.id
    settings = get_user_settings(load_subscriptions(), uid)
    await message.answer(
        settings_text(settings),
        parse_mode="HTML",
        reply_markup=settings_keyboard(settings),
    )


@router.callback_query(F.data == "settings:close")
async def close_settings(callback: CallbackQuery) -> None:
    """Delete the settings message, returning the user to the main-menu keyboard."""
    await callback.message.delete()
    await callback.answer("Настройки закрыты.")


@router.callback_query(F.data == "settings:autotrans")
async def toggle_autotrans(callback: CallbackQuery) -> None:
    """Toggle automatic translation of release notifications for this user."""
    uid = callback.from_user.id
    updated: dict = {}

    def _apply(subs: dict) -> None:
        current = get_user_settings(subs, uid)
        set_user_settings(subs, uid, auto_translate=not current["auto_translate"])
        updated["settings"] = get_user_settings(subs, uid)

    await atomic_update(_apply)
    logger.info("User %d set auto_translate=%s", uid, updated["settings"]["auto_translate"])
    await _show_settings(callback.message, updated["settings"])
    await callback.answer("Автоперевод включён." if updated["settings"]["auto_translate"] else "Автоперевод выключен.")


@router.callback_query(F.data.startswith("settings:lang:"))
async def set_language(callback: CallbackQuery) -> None:
    """Set the translation language from a preset button."""
    uid = callback.from_user.id
    lang = callback.data.split(":", 2)[2]

    def _apply(subs: dict) -> None:
        set_user_settings(subs, uid, translate_lang=lang)

    await atomic_update(_apply)
    logger.info("User %d set translate_lang=%s", uid, lang)
    settings = get_user_settings(load_subscriptions(), uid)
    await _show_settings(callback.message, settings)
    await callback.answer(f"Язык перевода: {lang}")


@router.callback_query(F.data == "settings:langcustom")
async def prompt_custom_language(callback: CallbackQuery, state: FSMContext) -> None:
    """Ask the user to type a language code, sending the full list of codes."""
    await state.set_state(SettingsStates.waiting_for_lang)
    await callback.message.edit_text(
        "Отправьте код языка перевода. Поддерживаемые коды:\n\n" + codes_overview(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(SettingsStates.waiting_for_lang)
async def process_custom_language(message: Message, state: FSMContext) -> None:
    """Validate the typed language code, save it, and reopen the menu."""
    uid = message.from_user.id
    canonical = normalize_lang(message.text or "")
    if not canonical:
        await message.answer(
            "Неверный код языка. Используйте один из кодов из списка выше (например <code>ru</code>, <code>en</code>).",
            parse_mode="HTML",
        )
        return

    def _apply(subs: dict) -> None:
        set_user_settings(subs, uid, translate_lang=canonical)

    await atomic_update(_apply)
    await state.clear()
    logger.info("User %d set translate_lang=%s (custom)", uid, canonical)
    settings = get_user_settings(load_subscriptions(), uid)
    await message.answer(
        settings_text(settings),
        parse_mode="HTML",
        reply_markup=settings_keyboard(settings),
    )


# ---------------------------------------------------------------------------
# Release translate/original toggle
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("reltr:"))
async def toggle_release_translation(callback: CallbackQuery) -> None:
    """Switch a release message between translation and original.

    Callback format: ``reltr:<rid>:<0|1>`` where the last field is the state
    currently displayed (1 = translated). The release is taken from
    RELEASE_CACHE; GitHub is never re-fetched here.
    """
    _, rid_str, cur_str = callback.data.split(":")
    rid = int(rid_str)
    currently_translated = cur_str == "1"
    entry = RELEASE_CACHE.get(rid)
    if not entry:
        await callback.answer(
            "Этот релиз уже не в памяти бота. Откройте его заново из списка репозитория.",
            show_alert=True,
        )
        return
    uid = callback.from_user.id
    settings = get_user_settings(load_subscriptions(), uid)
    new_state = not currently_translated
    await callback.answer("Перевожу…" if new_state else "Возвращаю оригинал…")
    html_out = await build_release_html(
        entry["release"],
        entry["repo_name"],
        entry["header"],
        settings["translate_lang"] if new_state else None,
    )
    await callback.message.edit_text(
        rich_message=InputRichMessage(html=html_out),
        reply_markup=release_translate_keyboard(rid, translated=new_state, repo_index=entry["repo_index"]),
    )
