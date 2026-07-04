"""Main entry point for Content Zavod Bot."""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent

from app.config import settings
from app.db.session import init_db, close_db, get_db_session
from app.scheduler import start_scheduler
from app.donor_parser import shutdown_parser_executor

# Import all handlers
from app.handlers import (
    start, channels, donors, ideas, ideas_settings,
    drafts, media, schedule, my_ideas,
    spaces, content_plan, analytics, youtube,
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


async def on_startup() -> None:
    """Startup handler."""
    logger.info("Starting Content Zavod Bot...")
    await init_db()
    logger.info("Database initialized")


async def on_shutdown() -> None:
    """Shutdown handler."""
    logger.info("Shutting down Content Zavod Bot...")
    shutdown_parser_executor()
    logger.info("Parser executor shut down")
    await close_db()
    logger.info("Database connections closed")


async def main() -> None:
    """Main bot function."""
    # Create bot instance
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
        ),
    )

    # Create dispatcher
    dp = Dispatcher(storage=MemoryStorage())

    # Register startup/shutdown handlers
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Middleware to inject database session
    @dp.update.middleware()
    async def db_session_middleware(handler, event, data):
        """Inject database session into handlers."""
        async for session in get_db_session():
            data["session"] = session
            return await handler(event, data)

    # Register routers
    dp.include_router(start.router)
    dp.include_router(channels.router)
    dp.include_router(donors.router)
    dp.include_router(ideas.router)
    dp.include_router(my_ideas.router)
    dp.include_router(ideas_settings.router)

    dp.include_router(drafts.router)
    dp.include_router(media.router)
    dp.include_router(schedule.router)
    dp.include_router(spaces.router)
    dp.include_router(content_plan.router)
    dp.include_router(analytics.router)
    dp.include_router(youtube.router)

    @dp.errors()
    async def global_error_handler(event: ErrorEvent):
        """Log unexpected errors."""
        logger.error("Unhandled error: %s", event.exception, exc_info=event.exception)

    # Start scheduler and parser as background tasks
    tasks = start_scheduler(bot)

    try:
        # Start polling
        logger.info("Bot started successfully!")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        # Cancel tasks
        # Cancel tasks
        for task in tasks:
            task.cancel()
        # Wait for tasks to finish
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Task error during shutdown: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
