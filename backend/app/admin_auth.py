"""Platform-operator admin login: username/password -> a JWT access + refresh
token pair. Distinct from end-user login (app/user_auth.py) - the `scope`
claim ("admin" here, "user" there) keeps the two kinds of token from ever
being accepted by the other's endpoints, even though both are backed by the
same RS256 keys and refresh-token machinery (app/jwt_tokens.py).

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
from fastapi import Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import jwt_tokens
from app.auth import GatewayError, extract_bearer_token
from app.config import get_settings
from app.models import AdminUser

SCOPE = "admin"


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_access_token(user: AdminUser) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    return jwt_tokens.encode({
        "sub": user.username,
        "admin_user_id": user.id,
        "scope": SCOPE,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
        "jti": str(uuid.uuid4()),
    })


async def create_refresh_token(user: AdminUser, db: AsyncSession) -> str:
    return await jwt_tokens.create_refresh_token(
        SCOPE, user.id, {"sub": user.username, "admin_user_id": user.id, "scope": SCOPE}, db
    )


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
    admin_user_id = await jwt_tokens.rotate_refresh_token(refresh_token, SCOPE, db)

    user_result = await db.execute(select(AdminUser).where(AdminUser.id == admin_user_id))
    user = user_result.scalar_one_or_none()
    if user is None or user.status != "active":
        raise GatewayError(401, "Account no longer active", "authentication_error")

    return await issue_token_pair(user, db)


async def revoke_refresh_token(refresh_token: str, db: AsyncSession) -> None:
    await jwt_tokens.revoke_refresh_token(refresh_token, db)


async def require_admin_jwt(authorization: str | None = Header(default=None)) -> dict:
    """FastAPI dependency gating every /admin/* route (except the auth routes
    themselves and /health). Purely stateless - signature + expiry only, no
    DB round trip - so it stays cheap on every admin request."""
    token = extract_bearer_token(authorization)
    claims = jwt_tokens.decode(token)
    if claims.get("type") != "access":
        raise GatewayError(401, "Not an access token", "authentication_error")
    if claims.get("scope") != SCOPE:
        raise GatewayError(401, "Not an admin token", "authentication_error")
    return claims
