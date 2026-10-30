import asyncio
import logging

import httpx
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import Settings
from bot.db import create_engine
from bot.handlers import create_router

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = Settings()
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT telegram_id FROM users LIMIT 0"))
            await connection.execute(text("SELECT id FROM turns LIMIT 0"))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with (
            httpx.AsyncClient(timeout=settings.ai_timeout_seconds) as client,
            Bot(settings.bot_token.get_secret_value()) as bot,
        ):
            if (await bot.get_webhook_info()).url:
                logger.error("Webhook is active. Disable it explicitly before starting polling.")
                raise SystemExit(1)
            await bot.set_my_commands(
                [
                    BotCommand(command="start", description="Начать"),
                    BotCommand(command="help", description="Помощь"),
                    BotCommand(command="clear", description="Удалить историю"),
                    BotCommand(command="id", description="Мой Telegram ID"),
                ]
            )
            dispatcher = Dispatcher()
            dispatcher.include_router(create_router(settings, sessions, client))
            logger.info("bot_started")
            await dispatcher.start_polling(bot, close_bot_session=False, tasks_concurrency_limit=64)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(main())
    except ValidationError as exc:
        fields = ", ".join(".".join(map(str, error["loc"])) for error in exc.errors())
        logger.error("Invalid or missing settings: %s", fields)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        logger.error("startup_or_polling_failed type=%s", type(exc).__name__)
        raise SystemExit(1) from None
