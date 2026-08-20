import asyncio
import uuid

import pytest

from app.auth import GatewayError
from app.auth_throttle import (
    clear_auth_failures,
    enforce_auth_rate_limit,
    enforce_not_locked_out,
    record_auth_failure,
)
from app.config import get_settings


def _identifier() -> str:
    return f"test-{uuid.uuid4().hex[:12]}"


async def test_attempts_under_the_ceiling_are_allowed(db):
    ident = _identifier()
    for _ in range(get_settings().auth_max_attempts_per_minute):
        await enforce_auth_rate_limit(db, "login", ident)


async def test_attempt_over_the_ceiling_is_rejected(db):
    ident = _identifier()
    limit = get_settings().auth_max_attempts_per_minute
    for _ in range(limit):
        await enforce_auth_rate_limit(db, "login", ident)

    with pytest.raises(GatewayError) as exc_info:
        await enforce_auth_rate_limit(db, "login", ident)
    assert exc_info.value.status_code == 429


async def test_the_counter_is_per_identifier(db):
    """One noisy client must not lock out everyone else."""
    noisy, quiet = _identifier(), _identifier()
    for _ in range(get_settings().auth_max_attempts_per_minute + 1):
        try:
            await enforce_auth_rate_limit(db, "login", noisy)
        except GatewayError:
            pass

    await enforce_auth_rate_limit(db, "login", quiet)


async def test_the_counter_is_per_scope(db):
    """A burst of signups shouldn't consume the login allowance for the same
    IP, or an attacker could deny logins by spamming a different endpoint."""
    ident = _identifier()
    for _ in range(get_settings().auth_max_attempts_per_minute + 1):
        try:
            await enforce_auth_rate_limit(db, "signup", ident)
        except GatewayError:
            pass

    await enforce_auth_rate_limit(db, "login", ident)


async def test_concurrent_attempts_cannot_over_admit(db, session_factory):
    """The same real-concurrency proof as the request rate limiter: the
    atomic upsert-and-increment must not let two concurrent attempts both
    read 'under limit' before either commits."""
    ident = _identifier()
    limit = get_settings().auth_max_attempts_per_minute

    async def attempt() -> bool:
        async with session_factory() as session:
            try:
                await enforce_auth_rate_limit(session, "login", ident)
                return True
            except GatewayError:
                return False

    results = await asyncio.gather(*[attempt() for _ in range(limit + 15)])
    assert sum(results) == limit


async def test_lockout_engages_after_repeated_failures(db):
    ident = _identifier()
    await enforce_not_locked_out(db, ident)  # clean slate is fine

    for _ in range(get_settings().auth_lockout_threshold):
        await record_auth_failure(db, ident)

    with pytest.raises(GatewayError) as exc_info:
        await enforce_not_locked_out(db, ident)
    assert exc_info.value.status_code == 429


async def test_failures_accumulate_on_one_row(db):
    """Regression test: an earlier draft keyed the lockout row by the current
    timestamp, so every failure created a fresh row and the streak never
    reached the threshold - the lockout silently never engaged."""
    ident = _identifier()
    threshold = get_settings().auth_lockout_threshold

    for _ in range(threshold - 1):
        await record_auth_failure(db, ident)
    await enforce_not_locked_out(db, ident)  # one short: still allowed

    await record_auth_failure(db, ident)
    with pytest.raises(GatewayError):
        await enforce_not_locked_out(db, ident)


async def test_successful_login_clears_the_streak(db):
    """A user who mistypes a few times then gets it right shouldn't carry
    those failures toward a future lockout."""
    ident = _identifier()
    for _ in range(get_settings().auth_lockout_threshold):
        await record_auth_failure(db, ident)

    await clear_auth_failures(db, ident)
    await enforce_not_locked_out(db, ident)


async def test_lockout_is_per_identifier(db):
    locked, other = _identifier(), _identifier()
    for _ in range(get_settings().auth_lockout_threshold):
        await record_auth_failure(db, locked)

    await enforce_not_locked_out(db, other)
