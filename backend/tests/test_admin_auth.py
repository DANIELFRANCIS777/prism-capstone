import asyncio
import uuid

import pytest

from app.admin_auth import create_refresh_token, rotate_refresh_token
from app.auth import GatewayError
from app.models import AdminUser


async def _make_admin_user(db) -> AdminUser:
    user = AdminUser(
        username=f"test-admin-{uuid.uuid4().hex[:8]}",
        password_hash="not-checked-by-this-test",
        status="active",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def test_refresh_token_rotation_is_single_use_under_concurrency(db, session_factory):
    """Regression test: rotate_refresh_token used to SELECT-then-check-then-
    UPDATE, so two concurrent requests with the same refresh token could both
    pass the "not yet used" check before either committed. Fires 20 concurrent
    rotation attempts against the same token and asserts exactly one wins -
    the same real-concurrency proof pattern as the rate limiter's test."""
    user = await _make_admin_user(db)
    token = await create_refresh_token(user, db)

    async def attempt() -> bool:
        async with session_factory() as session:
            try:
                await rotate_refresh_token(token, session)
                return True
            except GatewayError:
                return False

    results = await asyncio.gather(*[attempt() for _ in range(20)])
    assert sum(results) == 1


async def test_reused_refresh_token_is_rejected_and_revokes_other_active_tokens(db):
    user = await _make_admin_user(db)
    token = await create_refresh_token(user, db)

    first = await rotate_refresh_token(token, db)  # consumes `token`, issues a new pair

    with pytest.raises(GatewayError) as exc_info:
        await rotate_refresh_token(token, db)  # reuse of an already-consumed token
    assert exc_info.value.status_code == 401

    # The theft-response should have revoked the new token issued above too.
    with pytest.raises(GatewayError):
        await rotate_refresh_token(first["refresh_token"], db)
