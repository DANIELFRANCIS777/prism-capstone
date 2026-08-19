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
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db import engine as app_engine
from app.jwt_keys import ensure_keys_exist
from app.migrate import run_migrations
from app.models import VirtualKey

# Only main.py's lifespan normally generates the RS256 key pair; pytest never
# runs that, so admin-auth tests (JWT signing) need it done explicitly once.
ensure_keys_exist()


def make_virtual_key(**overrides) -> VirtualKey:
    """Shared VirtualKey factory for tests - a unique virtual_key per call
    (tests that persist it need that), with sensible defaults callers can
    override piecemeal."""
    fields = dict(
        virtual_key=f"test-{uuid.uuid4().hex[:8]}",
        team="test",
        monthly_budget_usd=100,
        requests_per_minute=10,
        model_allowlist=["fast"],
        status="active",
    )
    fields.update(overrides)
    return VirtualKey(**fields)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    # Build the schema the same way production does - through the migrations -
    # so a revision that's missing or wrong fails the suite instead of being
    # papered over by create_all against the live models.
    await run_migrations()
    # run_migrations() borrows the app's normally-pooled engine. Nothing else in
    # the suite uses it, so hand its connections back immediately instead of
    # holding one open for the whole session.
    await app_engine.dispose()

    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
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
