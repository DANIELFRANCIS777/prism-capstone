from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import VirtualKey


class GatewayError(HTTPException):
    """HTTPException whose body matches the OpenAI-style error shape from
    docs/API_CONTRACT.md: {"error": {"message", "type", "code"}}."""

    def __init__(self, status_code: int, message: str, error_type: str):
        super().__init__(
            status_code=status_code,
            detail={"error": {"message": message, "type": error_type, "code": error_type}},
        )


def extract_bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise GatewayError(401, "Missing bearer token", "authentication_error")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise GatewayError(401, "Missing bearer token", "authentication_error")
    return token


async def authenticate(authorization: str | None, db: AsyncSession) -> VirtualKey:
    """Plain function (not a FastAPI dependency) so callers can catch the
    failure and log the rejected request before re-raising it."""
    token = extract_bearer_token(authorization)
    result = await db.execute(select(VirtualKey).where(VirtualKey.virtual_key == token))
    key = result.scalar_one_or_none()
    if key is None or key.status != "active":
        raise GatewayError(401, "Invalid or inactive virtual key", "authentication_error")
    return key


def enforce_allowlist(key: VirtualKey, requested_model: str) -> None:
    if requested_model not in key.model_allowlist:
        raise GatewayError(
            403,
            f"Model '{requested_model}' is not on this key's allowlist",
            "model_not_allowed",
        )
