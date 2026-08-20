"""Brute-force and abuse protection for the unauthenticated auth endpoints.

/auth/login, /auth/signup, and /admin/auth/login had no throttling of any
kind. That's a credential-stuffing surface against the single bootstrap
admin account (which can read every tenant's usage and logs), and a
signup-spam surface where each fake org gets the platform-funded budget
ceiling.

Two independent mechanisms, because they stop different things:

  * A per-minute attempt ceiling per identifier (client IP, and for login
    also the submitted email), which caps request *volume* regardless of
    outcome. Uses the same atomic upsert-and-increment as
    app/rate_limit.py - Postgres serializes the row write, so concurrent
    attempts each get a distinct count and can't both slip under the limit.

  * A consecutive-failure lockout per account identifier, which stops slow
    distributed guessing that stays under the per-minute ceiling. Cleared on
    a successful login, so a legitimate user who mistypes a few times isn't
    punished once they get it right.

Both are keyed on the identifier rather than tied to a user row, so they
work for a login attempt against an email that doesn't exist - which is
exactly what enumeration looks like.
"""

import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import GatewayError
from app.config import get_settings
from app.models import AuthAttempt

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60

# Lockout rows are a running streak per identifier, not a time bucket, so
# they pin window_start to a sentinel to keep the composite PK on one row.
# Recency is tracked by last_failure_at instead.
LOCKOUT_SCOPE = "lockout"
LOCKOUT_WINDOW = 0


def _window_start() -> int:
    now = int(time.time())
    return now - (now % WINDOW_SECONDS)


def client_identifier(request) -> str:
    """Best-effort client IP.

    Behind a proxy (Fly, nginx, Cloudflare) request.client.host is the
    proxy's own address, which would make every caller share one throttle
    bucket - so prefer the first hop in X-Forwarded-For. That header is
    client-controllable when the app is exposed directly, meaning a
    determined attacker on a direct deployment can rotate it to dodge the
    IP limit. The per-email limit and the lockout are the defenses that
    still hold in that case; this one is about cheap volume control, and
    trusting the header is the right trade for the proxied deployments this
    is actually aimed at."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def enforce_auth_rate_limit(db: AsyncSession, scope: str, identifier: str) -> None:
    """Per-identifier request ceiling for one auth endpoint. `scope` separates
    counters so a burst of signups can't lock out logins."""
    settings = get_settings()

    stmt = pg_insert(AuthAttempt).values(
        scope=scope, identifier=identifier, window_start=_window_start(), attempts=1
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[AuthAttempt.scope, AuthAttempt.identifier, AuthAttempt.window_start],
        set_={"attempts": AuthAttempt.attempts + 1},
    ).returning(AuthAttempt.attempts)

    attempts = (await db.execute(stmt)).scalar_one()
    await db.commit()

    if attempts > settings.auth_max_attempts_per_minute:
        logger.warning(
            "auth rate limit exceeded",
            extra={"scope": scope, "identifier": identifier, "attempts": attempts},
        )
        raise GatewayError(
            429,
            "Too many attempts. Please wait a minute and try again.",
            "rate_limit_exceeded",
        )


async def enforce_not_locked_out(db: AsyncSession, identifier: str) -> None:
    """Rejects before any password check when an account identifier has too
    many recent consecutive failures.

    The streak expires by `last_failure_at`, not by when it started, so the
    lockout window is measured from the most recent failure - a guesser who
    keeps trying stays locked out rather than getting a fresh allowance once
    the original streak ages out."""
    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.auth_lockout_minutes)

    result = await db.execute(
        select(AuthAttempt.failures).where(
            AuthAttempt.scope == LOCKOUT_SCOPE,
            AuthAttempt.identifier == identifier,
            AuthAttempt.last_failure_at >= cutoff,
        )
    )
    failures = result.scalar_one_or_none() or 0
    if failures >= settings.auth_lockout_threshold:
        logger.warning(
            "auth lockout active", extra={"identifier": identifier, "failures": failures}
        )
        raise GatewayError(
            429,
            f"Too many failed attempts. Try again in {settings.auth_lockout_minutes} minutes.",
            "rate_limit_exceeded",
        )


async def record_auth_failure(db: AsyncSession, identifier: str) -> None:
    """Exactly one lockout row per identifier - window_start is pinned to the
    LOCKOUT_WINDOW sentinel (not a real timestamp) so every failure lands on
    the same row and the counter actually accumulates. Recency lives in
    last_failure_at instead."""
    now = datetime.now(timezone.utc)
    stmt = pg_insert(AuthAttempt).values(
        scope=LOCKOUT_SCOPE,
        identifier=identifier,
        window_start=LOCKOUT_WINDOW,
        failures=1,
        last_failure_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[AuthAttempt.scope, AuthAttempt.identifier, AuthAttempt.window_start],
        set_={"failures": AuthAttempt.failures + 1, "last_failure_at": now},
    )
    await db.execute(stmt)
    await db.commit()


async def clear_auth_failures(db: AsyncSession, identifier: str) -> None:
    """A successful login ends the streak - a user who mistyped twice then got
    it right shouldn't carry those failures toward a future lockout."""
    await db.execute(
        delete(AuthAttempt).where(
            AuthAttempt.scope == LOCKOUT_SCOPE, AuthAttempt.identifier == identifier
        )
    )
    await db.commit()
