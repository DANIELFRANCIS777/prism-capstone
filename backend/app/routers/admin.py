from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_auth import (
    authenticate_admin,
    issue_token_pair,
    require_admin_jwt,
    revoke_refresh_token,
    rotate_refresh_token,
)
from app.db import get_db
from app.models import RequestLog

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.post("/admin/auth/login")
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    user = await authenticate_admin(body.username, body.password, db)
    return await issue_token_pair(user, db)


@router.post("/admin/auth/refresh")
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """Rotates the refresh token: the one passed in is consumed (single-use)
    and a brand new access + refresh pair is returned."""
    return await rotate_refresh_token(body.refresh_token, db)


@router.post("/admin/auth/logout")
async def logout(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """Revokes a refresh token so it can no longer be used to mint new access
    tokens. Any access token already issued stays valid until it naturally
    expires (at most 30 minutes) - that's the accepted trade-off of stateless
    access tokens."""
    await revoke_refresh_token(body.refresh_token, db)
    return {"status": "ok"}


@router.get("/admin/usage", dependencies=[Depends(require_admin_jwt)])
async def usage_summary(
    key: str,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    conditions = [RequestLog.virtual_key == key]
    if from_:
        conditions.append(RequestLog.created_at >= datetime.fromisoformat(from_))
    if to:
        conditions.append(RequestLog.created_at <= datetime.fromisoformat(to))

    stmt = select(
        func.count(RequestLog.id).label("requests"),
        func.coalesce(func.sum(RequestLog.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(RequestLog.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(RequestLog.cost_usd), 0).label("cost_usd"),
        func.count(RequestLog.id).filter(RequestLog.cache == "hit").label("cache_hits"),
    ).where(*conditions)

    row = (await db.execute(stmt)).one()
    return {
        "key": key,
        "from": from_,
        "to": to,
        "requests": row.requests,
        "prompt_tokens": row.prompt_tokens,
        "completion_tokens": row.completion_tokens,
        "cost_usd": float(row.cost_usd),
        "cache_hits": row.cache_hits,
    }


@router.get("/admin/logs", dependencies=[Depends(require_admin_jwt)])
async def recent_logs(
    key: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(RequestLog).order_by(RequestLog.created_at.desc()).limit(limit)
    if key:
        stmt = stmt.where(RequestLog.virtual_key == key)

    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "request_id": r.request_id,
            "virtual_key": r.virtual_key,
            "requested_model": r.requested_model,
            "resolved_provider": r.resolved_provider,
            "resolved_model": r.resolved_model,
            "status": r.status,
            "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens,
            "cost_usd": float(r.cost_usd),
            "cache": r.cache,
            "fallback": r.fallback,
            "route_reason": r.route_reason,
            "retries": r.retries,
            "latency_ms": r.latency_ms,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.get("/admin/cache/stats", dependencies=[Depends(require_admin_jwt)])
async def cache_stats(key: str | None = None, db: AsyncSession = Depends(get_db)):
    conditions = [RequestLog.status == "ok"]
    if key:
        conditions.append(RequestLog.virtual_key == key)

    stmt = select(
        func.count(RequestLog.id).filter(RequestLog.cache == "hit").label("hits"),
        func.count(RequestLog.id).filter(RequestLog.cache == "miss").label("misses"),
    ).where(*conditions)

    row = (await db.execute(stmt)).one()
    total = row.hits + row.misses
    return {
        "key": key,
        "hits": row.hits,
        "misses": row.misses,
        "hit_rate": round(row.hits / total, 4) if total else 0.0,
    }
