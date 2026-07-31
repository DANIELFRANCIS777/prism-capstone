import asyncio

from sqlalchemy import select

from app.auth import GatewayError
from app.models import VirtualKey
from app.rate_limit import enforce_rate_limit
from tests.conftest import make_virtual_key


async def test_rate_limiter_admits_exactly_the_limit_under_real_concurrency(db, session_factory):
    """The claim this whole design rests on: no read-then-write gap. Proving
    it sequentially would be meaningless (of course 5 sequential calls admit
    exactly 5) - this fires 20 *concurrent* asyncio tasks, each with its own
    DB session (mirroring how 20 real concurrent requests would each get
    their own session), against a limit of 5, and asserts exactly 5 get in."""
    key = make_virtual_key(requests_per_minute=5)
    db.add(key)
    await db.commit()
    await db.refresh(key)

    async def attempt() -> bool:
        async with session_factory() as session:
            result = await session.execute(select(VirtualKey).where(VirtualKey.id == key.id))
            fetched_key = result.scalar_one()
            try:
                await enforce_rate_limit(session, fetched_key)
                return True
            except GatewayError:
                return False

    results = await asyncio.gather(*[attempt() for _ in range(20)])
    assert sum(results) == 5


async def test_rate_limiter_uses_a_fresh_window_per_key(db):
    """Two different keys must not share admission counts."""
    key_a = make_virtual_key(requests_per_minute=1)
    key_b = make_virtual_key(requests_per_minute=1)
    db.add_all([key_a, key_b])
    await db.commit()
    await db.refresh(key_a)
    await db.refresh(key_b)

    await enforce_rate_limit(db, key_a)  # consumes key_a's only slot
    await enforce_rate_limit(db, key_b)  # key_b is unaffected
