import asyncio
import uuid

import pytest

from app.auth import GatewayError
from app.models import Organization, User
from app.user_auth import create_refresh_token, require_user_jwt, rotate_refresh_token


async def _make_user(db) -> User:
    org = Organization(name="test org")
    db.add(org)
    await db.flush()
    user = User(
        email=f"test-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="not-checked-by-this-test",
        org_id=org.id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def test_refresh_token_rotation_is_single_use_under_concurrency(db, session_factory):
    """Mirrors test_admin_auth.py's concurrent-rotation regression test,
    scoped to subject_type="user" - proves the shared jwt_tokens.py machinery
    behaves identically for the new subject space."""
    user = await _make_user(db)
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
    user = await _make_user(db)
    token = await create_refresh_token(user, db)

    first = await rotate_refresh_token(token, db)

    with pytest.raises(GatewayError) as exc_info:
        await rotate_refresh_token(token, db)
    assert exc_info.value.status_code == 401

    with pytest.raises(GatewayError):
        await rotate_refresh_token(first["refresh_token"], db)


async def test_user_token_is_rejected_by_require_user_jwt_if_wrong_scope():
    """An admin-scoped access token must never pass as a user token, even
    though both are signed with the same RS256 keys."""
    from app import jwt_tokens
    from datetime import datetime, timedelta, timezone

    admin_scoped_token = jwt_tokens.encode({
        "sub": "someone",
        "scope": "admin",
        "type": "access",
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        "jti": str(uuid.uuid4()),
    })
    with pytest.raises(GatewayError) as exc_info:
        await require_user_jwt(f"Bearer {admin_scoped_token}")
    assert exc_info.value.status_code == 401
