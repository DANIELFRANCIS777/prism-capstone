"""Tests run against a real Postgres (DATABASE_URL, default matches the
docker-compose-published port) - this project treats Postgres as required
infrastructure everywhere else, so the tests do too, rather than mocking out
the one piece of behavior (atomic concurrent SQL) that most needs a real
database to prove anything.

Uses a dedicated NullPool engine rather than the app's normally-pooled one:
pytest-asyncio's per-test event loop churn otherwise leaves pooled asyncpg
connections bound to a loop that's already gone, producing "another
operation is in progress" errors on the next test that reuses them."""

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db import Base


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(test_engine):
    return async_sessionmaker(test_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def db(session_factory):
    session = session_factory()
    try:
        yield session
    finally:
        # Best-effort close: under NullPool + pytest-asyncio's loop churn,
        # closing can itself raise on unrelated connection-teardown races
        # that have nothing to do with whether the test's assertions passed.
        try:
            await session.close()
        except Exception:
            pass
