"""Application entry point. Initialises the bot, connects handlers, and starts polling.

Supports graceful shutdown on Ctrl+C by cancelling the background checker task.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession

from core import config
from github.checker import background_checker, run_check_cycle
from ui.handlers import router
from ui.settings import router as settings_router

logger = logging.getLogger(__name__)


async def main() -> None:
    """Set up the bot, register the router, start the background checker, and begin polling."""
    # Create logs directory if it doesn't exist
    log_dir = Path(config.LOG_DIR)
    log_dir.mkdir(exist_ok=True)

    # Configure root logger with console and rotating file handlers
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        log_dir / config.LOG_FILE,
        maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    logging.getLogger("aiogram.event").setLevel(logging.WARNING)

    if config.PROXY_URL:
        session = AiohttpSession(proxy=config.PROXY_URL)
        bot = Bot(token=config.BOT_TOKEN, session=session)
    else:
        bot = Bot(token=config.BOT_TOKEN)

    # Verify Telegram API access before starting any long-running work. This
    # turns a silent 60s hang inside start_polling (bot.me() with no error
    # surfaced) into an immediate, actionable failure.
    try:
        me = await bot.me()
    except Exception:
        logger.exception("Failed to authorize bot with Telegram API (proxy=%r)", config.PROXY_URL or None)
        await bot.session.close()
        raise
    logger.info("Bot authorized as @%s (id=%s)", me.username, me.id)

    # One‑shot mode: run a single check and exit
    if "--once" in sys.argv:
        await run_check_cycle(bot)
        await bot.session.close()
        return

    dp = Dispatcher()
    dp.include_router(router)
    dp.include_router(settings_router)

    # Launch the background checker and polling as named tasks so their
    # lifecycle is observable: a polling crash must abort the process instead
    # of being swallowed by the event loop.
    checker_task = asyncio.create_task(background_checker(bot), name="checker")
    polling_task = asyncio.create_task(dp.start_polling(bot), name="polling")

    # Unix: add_signal_handler enables graceful SIGINT/SIGTERM. On Windows it
    # raises NotImplementedError (suppressed); asyncio.run's KeyboardInterrupt
    # handling still reaches the finally block below for a clean shutdown.
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received, stopping...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _signal_handler)

    stop_task = asyncio.create_task(stop_event.wait(), name="stop_event")

    try:
        # Wait for whichever happens first: shutdown requested, polling crashed,
        # or the background checker crashed (it should never return normally).
        done, _ = await asyncio.wait(
            {polling_task, checker_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            if task is stop_task:
                logger.info("Stop event received, shutting down...")
                continue
            exc = task.exception() if not task.cancelled() else None
            if exc is not None:
                logger.error("%s task crashed", task.get_name(), exc_info=exc)
    finally:
        for task in (polling_task, checker_task, stop_task):
            task.cancel()
        await asyncio.gather(polling_task, checker_task, stop_task, return_exceptions=True)
        await bot.session.close()
        logger.info("Bot stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
