import time

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import GatewayError
from app.models import RateLimitWindow, VirtualKey


async def enforce_rate_limit(db: AsyncSession, key: VirtualKey) -> None:
    """Atomic upsert-and-increment against a fixed 60s window row. Postgres
    serializes the row write, so concurrent requests each get a distinct count -
    no read-then-write gap for two requests to both see 'under limit'."""
    now = int(time.time())
    window_start = now - (now % 60)

    stmt = pg_insert(RateLimitWindow).values(
        virtual_key=key.virtual_key, window_start=window_start, count=1
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[RateLimitWindow.virtual_key, RateLimitWindow.window_start],
        set_={"count": RateLimitWindow.count + 1},
    ).returning(RateLimitWindow.count)

    result = await db.execute(stmt)
    count = result.scalar_one()
    await db.commit()

    if count > key.requests_per_minute:
        raise GatewayError(
            429,
            f"Rate limit of {key.requests_per_minute} requests/minute exceeded",
            "rate_limit_exceeded",
        )
