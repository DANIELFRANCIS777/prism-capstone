from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    get_settings().database_url,
    echo=False,
    # Serverless Postgres (Neon, Aurora Serverless) suspends after a few
    # minutes of inactivity, which silently kills pooled connections. Without
    # pre-ping the first request after an idle period fails on a dead socket
    # rather than transparently reconnecting - a self-inflicted error on
    # exactly the request most likely to be a real user arriving.
    pool_pre_ping=True,
    # Also bound connection age, so a connection that a proxy or the
    # database has quietly dropped can't linger in the pool indefinitely.
    pool_recycle=300,
)
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session
