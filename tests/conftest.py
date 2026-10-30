import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, Update, User
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.config import Settings
from bot.handlers import create_router


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        bot_token="123456789:test-token",
        ai_api_key="test-api-key",
        ai_model="test-model",
        postgres_password="test-password",
        allowed_user_ids="101,202",
        allow_all_users=False,
        request_interval_seconds=0,
    )


@pytest.fixture
async def sessions():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration tests")
    schema = f"test_{uuid4().hex}"
    admin = create_async_engine(url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})

    def migrate(connection):
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")

    try:
        async with engine.begin() as connection:
            await connection.run_sync(migrate)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


class TelegramSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.messages: list[SendMessage] = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, SendMessage):
            self.messages.append(method)
            return Message(
                message_id=len(self.messages),
                date=datetime.now(UTC),
                chat=Chat(id=method.chat_id, type="private"),
                text=method.text,
            )
        return True

    async def stream_content(self, *args, **kwargs) -> AsyncGenerator[bytes]:
        yield b""


@pytest.fixture
async def telegram(settings, sessions):
    session = TelegramSession()
    bot = Bot(settings.bot_token.get_secret_value(), session=session)
    dispatchers = []

    def setup(client):
        dispatcher = Dispatcher()
        dispatcher.include_router(create_router(settings, sessions, client))
        dispatchers.append(dispatcher)

        async def send(text_value, user_id=101, chat_type="private"):
            update = Update(
                update_id=len(session.messages) + 1,
                message=Message(
                    message_id=1,
                    date=datetime.now(UTC),
                    chat=Chat(id=user_id, type=chat_type),
                    from_user=User(id=user_id, is_bot=False, first_name="Test"),
                    text=text_value,
                ),
            )
            await dispatcher.feed_update(bot, update)

        return send, session.messages

    yield setup
    for dispatcher in dispatchers:
        await dispatcher.storage.close()
    await bot.session.close()
