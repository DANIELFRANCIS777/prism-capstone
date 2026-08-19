"""End-user (self-serve tenant) login: email/password -> a JWT access +
refresh token pair. Mirrors app/admin_auth.py's shape but is a distinct
subject space - the `scope` claim ("user" here, "admin" there) keeps a
signed-in tenant from ever hitting an /admin/* route, and vice versa, even
though both ride the same RS256 keys and refresh-token machinery
(app/jwt_tokens.py)."""

import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import jwt_tokens
from app.auth import GatewayError, extract_bearer_token
from app.config import get_settings
from app.models import User

SCOPE = "user"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_access_token(user: User) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    return jwt_tokens.encode({
        "sub": user.email,
        "user_id": user.id,
        "org_id": user.org_id,
        "scope": SCOPE,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
        "jti": str(uuid.uuid4()),
    })


async def create_refresh_token(user: User, db: AsyncSession) -> str:
    return await jwt_tokens.create_refresh_token(
        SCOPE, user.id, {"sub": user.email, "user_id": user.id, "org_id": user.org_id, "scope": SCOPE}, db
    )


async def issue_token_pair(user: User, db: AsyncSession) -> dict:
    settings = get_settings()
    return {
        "access_token": create_access_token(user),
        "refresh_token": await create_refresh_token(user, db),
        "token_type": "bearer",
        "expires_in": settings.access_token_expire_minutes * 60,
    }


async def authenticate_user(email: str, password: str, db: AsyncSession) -> User:
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None or user.status != "active" or not verify_password(password, user.password_hash):
        raise GatewayError(401, "Invalid email or password", "authentication_error")
    return user


async def rotate_refresh_token(refresh_token: str, db: AsyncSession) -> dict:
    user_id = await jwt_tokens.rotate_refresh_token(refresh_token, SCOPE, db)

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or user.status != "active":
        raise GatewayError(401, "Account no longer active", "authentication_error")

    return await issue_token_pair(user, db)


async def revoke_refresh_token(refresh_token: str, db: AsyncSession) -> None:
    await jwt_tokens.revoke_refresh_token(refresh_token, db)


async def require_user_jwt(authorization: str | None = Header(default=None)) -> dict:
    """FastAPI dependency gating every /me/* route. Purely stateless -
    signature + expiry only, no DB round trip."""
    token = extract_bearer_token(authorization)
    claims = jwt_tokens.decode(token)
    if claims.get("type") != "access":
        raise GatewayError(401, "Not an access token", "authentication_error")
    if claims.get("scope") != SCOPE:
        raise GatewayError(401, "Not a user token", "authentication_error")
    return claims
