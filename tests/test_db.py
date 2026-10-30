from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select

from bot import db


async def test_migration_matches_models(sessions):
    async with sessions.kw["bind"].connect() as connection:
        differences = await connection.run_sync(
            lambda sync: compare_metadata(MigrationContext.configure(sync), db.Base.metadata)
        )
    assert differences == []


async def test_history_order_limit_and_isolation(sessions):
    for question in ("first", "second", "third"):
        turn_id = await db.save_question(sessions, 101, question)
        await db.finish_turn(sessions, turn_id, answer=f"answer to {question}")
    other = await db.save_question(sessions, 202, "private")
    await db.finish_turn(sessions, other, answer="private answer")
    failed = await db.save_question(sessions, 101, "failed")
    await db.finish_turn(sessions, failed, error="timeout")

    turns = await db.recent_turns(sessions, 101, 2)
    assert [turn.question for turn in turns] == ["second", "third"]
    assert await db.recent_turns(sessions, 101, 0) == []
    assert await db.recent_turns(sessions, 303, 10) == []
    assert 0 <= await db.seconds_since_last_question(sessions, 101) < 10


async def test_clear_removes_only_own_history_including_failed(sessions):
    await db.save_question(sessions, 101, "mine")
    await db.save_question(sessions, 202, "someone else's")
    await db.clear_history(sessions, 101)
    async with sessions() as session:
        turns = (await session.scalars(select(db.Turn))).all()
    assert len(turns) == 1
    assert turns[0].user_id == 202


async def test_registration_is_idempotent_and_accepts_big_ids(sessions):
    await db.register_user(sessions, 7_000_000_000)
    await db.register_user(sessions, 7_000_000_000)
    async with sessions() as session:
        assert await session.get(db.User, 7_000_000_000) is not None
