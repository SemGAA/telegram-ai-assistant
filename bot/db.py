from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Text, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from bot.config import DatabaseSettings


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Turn(Base):
    __tablename__ = "turns"
    __table_args__ = (Index("ix_turns_user_id_id", "user_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.telegram_id", ondelete="CASCADE"))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def create_engine(settings: DatabaseSettings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=10,
        hide_parameters=True,
        connect_args={"timeout": 10, "command_timeout": 10},
    )


async def register_user(sessions: async_sessionmaker, user_id: int) -> None:
    async with sessions.begin() as session:
        await session.execute(insert(User).values(telegram_id=user_id).on_conflict_do_nothing())


async def recent_turns(sessions: async_sessionmaker, user_id: int, limit: int) -> list[Turn]:
    async with sessions() as session:
        rows = await session.scalars(
            select(Turn)
            .where(Turn.user_id == user_id, Turn.answer.is_not(None))
            .order_by(Turn.id.desc())
            .limit(limit)
        )
        return list(reversed(rows.all()))


async def seconds_since_last_question(sessions: async_sessionmaker, user_id: int) -> float | None:
    async with sessions() as session:
        return await session.scalar(
            select(func.extract("epoch", func.now() - Turn.created_at))
            .where(Turn.user_id == user_id)
            .order_by(Turn.id.desc())
            .limit(1)
        )


async def save_question(sessions: async_sessionmaker, user_id: int, question: str) -> int:
    async with sessions.begin() as session:
        await session.execute(insert(User).values(telegram_id=user_id).on_conflict_do_nothing())
        turn = Turn(user_id=user_id, question=question)
        session.add(turn)
        await session.flush()
        return turn.id


async def finish_turn(
    sessions: async_sessionmaker,
    turn_id: int,
    *,
    answer: str | None = None,
    error: str | None = None,
) -> None:
    async with sessions.begin() as session:
        await session.execute(
            update(Turn).where(Turn.id == turn_id).values(answer=answer, error=error)
        )


async def clear_history(sessions: async_sessionmaker, user_id: int) -> None:
    async with sessions.begin() as session:
        await session.execute(delete(Turn).where(Turn.user_id == user_id))
