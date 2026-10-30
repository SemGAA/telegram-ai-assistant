import asyncio

from alembic import context

from bot.config import DatabaseSettings
from bot.db import Base, create_engine


def run_migrations(connection):
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_online():
    engine = create_engine(DatabaseSettings())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_migrations)
    finally:
        await engine.dispose()


if context.config.attributes.get("connection") is not None:
    run_migrations(context.config.attributes["connection"])
elif context.is_offline_mode():
    context.configure(
        url=DatabaseSettings().database_url,
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(run_online())
