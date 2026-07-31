"""Admin login: username/password -> a JWT access + refresh token pair.

Access tokens (30 min default) are stateless - verified purely by RS256
signature + expiry, no DB lookup, so they stay cheap to check on every admin
request. Refresh tokens are the opposite: each one is tracked server-side by
its `jti` claim in the refresh_tokens table, is single-use (rotated on every
call to /admin/auth/refresh), and can be revoked outright (logout). That's
what makes a stolen refresh token containable - a stolen access token is only
ever a 30-minute problem by design.
"""

import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Header
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import GatewayError, extract_bearer_token
from app.config import get_settings
from app.jwt_keys import load_private_key, load_public_key
from app.models import AdminUser, RefreshToken

ALGORITHM = "RS256"


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def _encode(claims: dict) -> str:
    return jwt.encode(claims, load_private_key(), algorithm=ALGORITHM)


def _decode(token: str) -> dict:
    try:
        return jwt.decode(token, load_public_key(), algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise GatewayError(401, "Token has expired", "authentication_error") from exc
    except jwt.InvalidTokenError as exc:
        raise GatewayError(401, "Invalid token", "authentication_error") from exc


def create_access_token(user: AdminUser) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    return _encode({
        "sub": user.username,
        "admin_user_id": user.id,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
        "jti": str(uuid.uuid4()),
    })


async def create_refresh_token(user: AdminUser, db: AsyncSession) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=settings.refresh_token_expire_days)
    jti = str(uuid.uuid4())
    token = _encode({
        "sub": user.username,
        "admin_user_id": user.id,
        "type": "refresh",
        "iat": now,
        "exp": expires_at,
        "jti": jti,
    })
    db.add(RefreshToken(jti=jti, admin_user_id=user.id, expires_at=expires_at))
    await db.commit()
    return token


async def issue_token_pair(user: AdminUser, db: AsyncSession) -> dict:
    settings = get_settings()
    return {
        "access_token": create_access_token(user),
        "refresh_token": await create_refresh_token(user, db),
        "token_type": "bearer",
        "expires_in": settings.access_token_expire_minutes * 60,
    }


async def authenticate_admin(username: str, password: str, db: AsyncSession) -> AdminUser:
    result = await db.execute(select(AdminUser).where(AdminUser.username == username))
    user = result.scalar_one_or_none()
    if user is None or user.status != "active" or not verify_password(password, user.password_hash):
        raise GatewayError(401, "Invalid username or password", "authentication_error")
    return user


async def rotate_refresh_token(refresh_token: str, db: AsyncSession) -> dict:
    """Validates, single-use-consumes, and replaces a refresh token. Reuse of
    an already-consumed or revoked token is treated as a theft signal: every
    other active token for that user is revoked too.

    Single-use is enforced by one atomic conditional UPDATE (mirroring
    rate_limit.py's pattern), not a SELECT-then-check-then-UPDATE - two
    concurrent requests presenting the same token can't both read "not yet
    used" before either commits; only one UPDATE can ever match the row."""
    claims = _decode(refresh_token)
    if claims.get("type") != "refresh":
        raise GatewayError(401, "Not a refresh token", "authentication_error")

    jti = claims["jti"]
    result = await db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.jti == jti,
            RefreshToken.used_at.is_(None),
            RefreshToken.revoked_at.is_(None),
        )
        .values(used_at=datetime.now(timezone.utc))
        .returning(RefreshToken.admin_user_id)
    )
    admin_user_id = result.scalar_one_or_none()
    await db.commit()

    if admin_user_id is None:
        # Unknown token, or a known one that's already used/revoked. For a
        # known token, treat reuse as a theft signal and revoke every other
        # active token for that user.
        existing = await db.execute(select(RefreshToken).where(RefreshToken.jti == jti))
        record = existing.scalar_one_or_none()
        if record is not None:
            await db.execute(
                update(RefreshToken)
                .where(
                    RefreshToken.admin_user_id == record.admin_user_id,
                    RefreshToken.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(timezone.utc))
            )
            await db.commit()
        raise GatewayError(
            401, "Refresh token unknown, already used, or revoked", "authentication_error"
        )

    user_result = await db.execute(select(AdminUser).where(AdminUser.id == admin_user_id))
    user = user_result.scalar_one_or_none()
    if user is None or user.status != "active":
        raise GatewayError(401, "Account no longer active", "authentication_error")

    return await issue_token_pair(user, db)


async def revoke_refresh_token(refresh_token: str, db: AsyncSession) -> None:
    try:
        claims = _decode(refresh_token)
    except GatewayError:
        return  # already invalid/expired - nothing to revoke
    jti = claims.get("jti")
    if not jti:
        return
    await db.execute(
        update(RefreshToken).where(RefreshToken.jti == jti).values(revoked_at=datetime.now(timezone.utc))
    )
    await db.commit()


async def require_admin_jwt(authorization: str | None = Header(default=None)) -> dict:
    """FastAPI dependency gating every /admin/* route (except the auth routes
    themselves and /health). Purely stateless - signature + expiry only, no
    DB round trip - so it stays cheap on every admin request."""
    token = extract_bearer_token(authorization)
    claims = _decode(token)
    if claims.get("type") != "access":
        raise GatewayError(401, "Not an access token", "authentication_error")
    return claims
