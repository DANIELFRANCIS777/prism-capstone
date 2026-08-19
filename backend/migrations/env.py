"""Alembic environment. Reads DATABASE_URL from app.config so there is no second
copy of the connection settings to drift from the app's.

Two entry paths:
  - CLI (`alembic upgrade head`) - builds its own async engine here.
  - In-process (app/migrate.py at startup) - hands in an already-open sync
    connection via config.attributes, which is also where the advisory lock
    that serializes concurrent replicas is held.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.db import Base

# Importing the models module is what populates Base.metadata - without it
# autogenerate would see an empty schema and propose dropping every table.
import app.models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
        await connection.commit()
    await engine.dispose()


def run_migrations_online() -> None:
    existing = config.attributes.get("connection", None)
    if existing is not None:
        # Already inside a running event loop's greenlet - asyncio.run() here
        # would raise, and the caller owns the transaction and the lock anyway.
        do_run_migrations(existing)
        return
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
