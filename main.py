"""Application entry point. Initialises the bot, connects handlers, and starts polling.

Supports graceful shutdown on Ctrl+C by cancelling the background checker task.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiogram import Bot, Dispatcher

from core import config
from github.checker import background_checker
from ui.handlers import router

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

    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    # Launch the background checker as a task so we can cancel it on shutdown
    checker_task = asyncio.create_task(background_checker(bot))

    # Setup signal handler for graceful termination
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received, stopping...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _signal_handler)

    try:
        # Start polling and wait until a stop signal is received
        polling_task = asyncio.create_task(dp.start_polling(bot))
        await stop_event.wait()
        polling_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await polling_task
    finally:
        checker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await checker_task

        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
