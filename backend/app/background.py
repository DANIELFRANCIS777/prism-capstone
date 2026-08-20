"""Periodic maintenance that keeps unbounded tables from growing forever.

Two tables accumulate a row per event and were never pruned by anything:
rate_limit_windows (one row per key per 60-second window) and refresh_tokens
(one row per login and per token rotation - every 30 minutes per active
user by default). Neither is read once it's stale, so both are pure growth.

Runs in-process rather than as an external scheduler deliberately: a
headline goal is that `docker compose up` gives a self-hoster a correct,
complete deployment with no extra moving parts. Concurrency across replicas
is handled the same way migrations handle it - a Postgres advisory lock, so
whichever replica gets there first does the work and the rest skip it
rather than all pruning at once.

This is also the intended home for the provider model-catalog sync (see
ROADMAP.md): same shape, same lock, different query.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import delete, text

from app.config import get_settings
from app.db import async_session, engine
from app.models import RateLimitWindow, RefreshToken

logger = logging.getLogger(__name__)

# Distinct from migrate.py's lock id - pruning and migrating shouldn't block
# each other, they touch different things.
_MAINTENANCE_LOCK_ID = 8_675_310


async def _try_lock(connection) -> bool:
    """pg_try_advisory_lock returns immediately instead of waiting: if another
    replica is already pruning, this one has nothing useful to wait around
    for, so it just skips this cycle."""
    result = await connection.execute(
        text("SELECT pg_try_advisory_lock(:id)"), {"id": _MAINTENANCE_LOCK_ID}
    )
    return bool(result.scalar())


async def prune_once() -> dict[str, int]:
    """One maintenance pass. Returns the row counts deleted, for logging and
    so tests can assert on real work rather than just "didn't raise"."""
    settings = get_settings()
    deleted = {"rate_limit_windows": 0, "refresh_tokens": 0}

    async with engine.connect() as connection:
        if not await _try_lock(connection):
            logger.debug("maintenance skipped; another replica holds the lock")
            return deleted
        await connection.commit()

        try:
            async with async_session() as session:
                # Windows older than the cutoff can never be read again -
                # enforce_rate_limit only ever looks at the current window.
                cutoff = int(time.time()) - settings.rate_limit_window_retention_seconds
                result = await session.execute(
                    delete(RateLimitWindow).where(RateLimitWindow.window_start < cutoff)
                )
                deleted["rate_limit_windows"] = result.rowcount or 0

                # An expired refresh token is already rejected on presentation
                # (the JWT's own exp fails first), so the row is dead weight.
                # Used/revoked-but-unexpired rows are deliberately kept: they
                # are what makes reuse-after-rotation detectable as theft.
                result = await session.execute(
                    delete(RefreshToken).where(
                        RefreshToken.expires_at < datetime.now(timezone.utc)
                    )
                )
                deleted["refresh_tokens"] = result.rowcount or 0

                await session.commit()
        finally:
            await connection.execute(
                text("SELECT pg_advisory_unlock(:id)"), {"id": _MAINTENANCE_LOCK_ID}
            )
            await connection.commit()

    if any(deleted.values()):
        logger.info("maintenance pruned stale rows", extra={"deleted": deleted})
    return deleted


async def maintenance_loop() -> None:
    """Never lets an error kill the loop - a failed pass (transient DB blip,
    lock contention) should mean "try again next interval", not "maintenance
    silently stops for the lifetime of this process"."""
    interval = get_settings().maintenance_interval_seconds
    while True:
        try:
            await asyncio.sleep(interval)
            await prune_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("maintenance pass failed; will retry next interval")
