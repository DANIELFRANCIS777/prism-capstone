"""RS256 JWT encode/decode and refresh-token lifecycle, shared by the
platform-operator admin login (app/admin_auth.py) and end-user login
(app/user_auth.py). Extracted from what was originally admin_auth.py-only
logic - the single-use atomic-rotation SQL is identical in shape for both,
keyed by (subject_type, subject_id) instead of admin_user_id alone.

Access tokens are stateless (signature + expiry only, no DB lookup) so they
stay cheap to check on every request. Refresh tokens are tracked server-side
by their `jti` claim in the refresh_tokens table, single-use (rotated on
every call), and revocable outright (logout) - that's what makes a stolen
refresh token containable, unlike a stolen access token (only ever a
short-lived problem by design)."""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import GatewayError
from app.config import get_settings
from app.jwt_keys import load_private_key, load_public_key
from app.models import RefreshToken

ALGORITHM = "RS256"


def encode(claims: dict) -> str:
    return jwt.encode(claims, load_private_key(), algorithm=ALGORITHM)


def decode(token: str) -> dict:
    try:
        return jwt.decode(token, load_public_key(), algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise GatewayError(401, "Token has expired", "authentication_error") from exc
    except jwt.InvalidTokenError as exc:
        raise GatewayError(401, "Invalid token", "authentication_error") from exc


async def create_refresh_token(
    subject_type: str, subject_id: int, extra_claims: dict, db: AsyncSession
) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=settings.refresh_token_expire_days)
    jti = str(uuid.uuid4())
    token = encode({
        **extra_claims,
        "type": "refresh",
        "iat": now,
        "exp": expires_at,
        "jti": jti,
    })
    db.add(
        RefreshToken(
            jti=jti, subject_type=subject_type, subject_id=subject_id, expires_at=expires_at
        )
    )
    await db.commit()
    return token


async def rotate_refresh_token(refresh_token: str, subject_type: str, db: AsyncSession) -> int:
    """Validates, single-use-consumes, and rotates a refresh token; returns
    the subject_id it belonged to (caller re-checks the subject is still
    active and issues a new pair). Reuse of an already-consumed or revoked
    token is treated as a theft signal: every other active token for that
    subject is revoked too.

    Single-use is enforced by one atomic conditional UPDATE (mirroring
    rate_limit.py's pattern), not a SELECT-then-check-then-UPDATE - two
    concurrent requests presenting the same token can't both read "not yet
    used" before either commits; only one UPDATE can ever match the row."""
    claims = decode(refresh_token)
    if claims.get("type") != "refresh":
        raise GatewayError(401, "Not a refresh token", "authentication_error")

    jti = claims["jti"]
    result = await db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.jti == jti,
            RefreshToken.subject_type == subject_type,
            RefreshToken.used_at.is_(None),
            RefreshToken.revoked_at.is_(None),
        )
        .values(used_at=datetime.now(timezone.utc))
        .returning(RefreshToken.subject_id)
    )
    subject_id = result.scalar_one_or_none()
    await db.commit()

    if subject_id is None:
        existing = await db.execute(
            select(RefreshToken).where(
                RefreshToken.jti == jti, RefreshToken.subject_type == subject_type
            )
        )
        record = existing.scalar_one_or_none()
        if record is not None:
            await db.execute(
                update(RefreshToken)
                .where(
                    RefreshToken.subject_type == subject_type,
                    RefreshToken.subject_id == record.subject_id,
                    RefreshToken.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(timezone.utc))
            )
            await db.commit()
        raise GatewayError(
            401, "Refresh token unknown, already used, or revoked", "authentication_error"
        )

    return subject_id


async def revoke_refresh_token(refresh_token: str, db: AsyncSession) -> None:
    try:
        claims = decode(refresh_token)
    except GatewayError:
        return  # already invalid/expired - nothing to revoke
    jti = claims.get("jti")
    if not jti:
        return
    await db.execute(
        update(RefreshToken).where(RefreshToken.jti == jti).values(revoked_at=datetime.now(timezone.utc))
    )
    await db.commit()
