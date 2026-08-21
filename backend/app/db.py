from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _is_transaction_pooled(url: str) -> bool:
    """Whether the URL points at a transaction-mode connection pooler.

    Neon's pooled endpoint is `...-pooler.<region>...`; Supabase's is
    `...pooler.supabase.com`. Both run PgBouncer, as do RDS Proxy and a
    hand-rolled pgbouncer, so `pgbouncer` in the host is treated the same."""
    host = url.split("@")[-1].lower()
    return "-pooler." in host or "pooler." in host or "pgbouncer" in host


def _engine_kwargs(url: str) -> dict:
    kwargs: dict = {
        "echo": False,
        # Serverless Postgres (Neon, Aurora Serverless) suspends after a few
        # minutes idle, which silently kills pooled connections. Without
        # pre-ping the first request after an idle period fails on a dead
        # socket instead of transparently reconnecting - an error on exactly
        # the request most likely to be a real user arriving.
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

    if not _is_transaction_pooled(url):
        return kwargs

    # PgBouncer in transaction mode hands consecutive statements to different
    # server connections, so a prepared statement created on one isn't there
    # for the next - asyncpg otherwise fails with "prepared statement
    # __asyncpg_stmt_N__ does not exist", and its numeric names can collide
    # across clients. Both fixes are straight from SQLAlchemy's asyncpg
    # dialect docs.
    kwargs["connect_args"] = {
        "statement_cache_size": 0,
        "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
    }
    kwargs["prepared_statement_cache_size"] = 0
    # SQLAlchemy's own warning: without NullPool, prepared statements pile up
    # behind the proxy. The pooler is already doing the pooling, so a second
    # pool on this side buys nothing.
    kwargs["poolclass"] = NullPool
    kwargs.pop("pool_pre_ping", None)
    kwargs.pop("pool_recycle", None)
    return kwargs


_database_url = get_settings().database_url
engine = create_async_engine(_database_url, **_engine_kwargs(_database_url))
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session
