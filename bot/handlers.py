import asyncio
import logging
from collections.abc import Iterator

import httpx
from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.types import ErrorEvent, Message
from aiogram.utils.chat_action import ChatActionSender
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot import db
from bot.ai import AIError, build_messages, generate_answer
from bot.config import Settings

logger = logging.getLogger(__name__)

HELP = (
    "Напишите вопрос текстом. Я отвечу с учётом предыдущих сообщений.\n\n"
    "/clear - удалить историю\n"
    "/id - ваш Telegram ID\n"
    "/help - помощь\n\n"
    "Вопросы и ответы сохраняются в базе. Для ответа вопрос и часть истории "
    "передаются сервису AI. /clear удаляет историю из базы бота, но не из Telegram "
    "и не из систем провайдера."
)


def split_text(text: str, limit: int = 4000) -> Iterator[str]:
    # Telegram entity offsets use UTF-16; count non-BMP characters as two units.
    start, size = 0, 0
    for index, char in enumerate(text):
        width = 2 if ord(char) > 0xFFFF else 1
        if size + width > limit:
            yield text[start:index]
            start, size = index, 0
        size += width
    if start < len(text):
        yield text[start:]


async def send_answer(message: Message, text: str) -> None:
    for part in split_text(text):
        try:
            await message.answer(part)
        except TelegramRetryAfter as exc:
            if exc.retry_after > 30:
                raise
            await asyncio.sleep(exc.retry_after)
            await message.answer(part)


def create_router(
    settings: Settings, sessions: async_sessionmaker, client: httpx.AsyncClient
) -> Router:
    router = Router()
    busy: set[int] = set()
    tasks: set[asyncio.Task] = set()
    allowed = settings.allowed_ids

    @router.shutdown()
    async def drain_requests():
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=45)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    @router.message.outer_middleware()
    async def access_and_errors(handler, message: Message, data):
        if (
            message.chat.type != ChatType.PRIVATE
            or not message.from_user
            or message.from_user.is_bot
        ):
            return
        user_id = message.from_user.id
        if not settings.allow_all_users and user_id not in allowed:
            await message.answer(
                f"Доступ закрыт. Передайте администратору ваш Telegram ID: {user_id}."
            )
            return
        if user_id in busy:
            await message.answer("Предыдущий запрос ещё выполняется. Дождитесь ответа.")
            return
        if len(busy) >= settings.max_concurrent_requests:
            await message.answer("Сейчас много запросов. Попробуйте чуть позже.")
            return
        busy.add(user_id)
        task = asyncio.current_task()
        tasks.add(task)
        try:
            return await handler(message, data)
        except SQLAlchemyError as exc:
            logger.error("database_error user_id=%s type=%s", user_id, type(exc).__name__)
            await message.answer("База данных временно недоступна. Попробуйте позже.")
        finally:
            busy.remove(user_id)
            tasks.discard(task)

    @router.message(CommandStart())
    async def start(message: Message):
        await db.register_user(sessions, message.from_user.id)
        await message.answer(HELP)

    @router.message(Command("help"))
    async def help_command(message: Message):
        await message.answer(HELP)

    @router.message(Command("id"))
    async def id_command(message: Message):
        await message.answer(f"Ваш Telegram ID: {message.from_user.id}")

    @router.message(Command("clear"))
    async def clear(message: Message):
        await db.clear_history(sessions, message.from_user.id)
        await message.answer("История удалена. Следующий вопрос начнёт новый диалог.")

    @router.message(F.text.startswith("/"))
    async def unknown_command(message: Message):
        await message.answer("Неизвестная команда. Список команд: /help")

    @router.message(F.text)
    async def answer(message: Message):
        question = message.text.strip()
        if not question:
            await message.answer("Напишите вопрос текстом.")
            return
        if len(question) > settings.max_input_chars:
            await message.answer(f"Сократите вопрос до {settings.max_input_chars} символов.")
            return
        user_id = message.from_user.id
        elapsed = await db.seconds_since_last_question(sessions, user_id)
        if elapsed is not None and elapsed < settings.request_interval_seconds:
            await message.answer("Слишком частые запросы. Подождите несколько секунд.")
            return
        turns = await db.recent_turns(sessions, user_id, settings.history_turns)
        turn_id = await db.save_question(sessions, user_id, question)
        try:
            async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
                result = await generate_answer(
                    client, settings, build_messages(settings, turns, question)
                )
        except AIError as exc:
            await db.finish_turn(sessions, turn_id, error=exc.code)
            logger.warning("ai_error turn_id=%s code=%s", turn_id, exc.code)
            await message.answer(exc.user_message)
            return
        await db.finish_turn(sessions, turn_id, answer=result)
        await send_answer(message, result)
        logger.info("answer_sent turn_id=%s", turn_id)

    @router.message()
    async def unsupported(message: Message):
        await message.answer("Пока я принимаю только текстовые вопросы.")

    @router.errors()
    async def unexpected_error(event: ErrorEvent):
        logger.error("update_failed type=%s", type(event.exception).__name__)
        if isinstance(event.exception, TelegramAPIError):
            return True
        if event.update.message:
            try:
                await event.update.message.answer("Не удалось обработать запрос. Попробуйте позже.")
            except TelegramAPIError:
                pass
        return True

    return router
