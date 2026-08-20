import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import GatewayError
from app.auth_throttle import (
    clear_auth_failures,
    client_identifier,
    enforce_auth_rate_limit,
    enforce_not_locked_out,
    record_auth_failure,
)

from app.admin_auth import (
    authenticate_admin,
    issue_token_pair,
    require_admin_jwt,
    revoke_refresh_token,
    rotate_refresh_token,
)
from app.db import get_db
from app.models import RequestLog, VirtualKey

logger = logging.getLogger(__name__)

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


@router.get("/health")
async def health():
    """Liveness only: is this process up. Deliberately checks nothing else -
    a dependency being down should not get the container killed and
    restarted, which wouldn't fix it. Use /ready for routing decisions."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(db: AsyncSession = Depends(get_db)):
    """Readiness: can this replica actually serve a request. Every data-plane
    request needs Postgres (auth, rate limit, budget, logging), so a replica
    that can't reach it should be pulled from the load balancer rather than
    accepting traffic it will only fail. Point orchestrator readiness probes
    here, not at /health."""
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        logger.exception("readiness check failed")
        return JSONResponse(
            status_code=503, content={"status": "not_ready", "database": "unavailable"}
        )
    return {"status": "ready", "database": "ok"}


@router.post("/admin/auth/login")
async def login(request: Request, body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Throttled harder in effect than the tenant login, because the blast
    radius is bigger: this account can read every tenant's usage and logs,
    and there is normally exactly one of them."""
    username = body.username.strip().lower()
    await enforce_auth_rate_limit(db, "admin_login", client_identifier(request))
    await enforce_not_locked_out(db, f"admin:{username}")

    try:
        user = await authenticate_admin(body.username, body.password, db)
    except GatewayError:
        await record_auth_failure(db, f"admin:{username}")
        raise

    await clear_auth_failures(db, f"admin:{username}")
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
    key_id: int,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    conditions = [RequestLog.virtual_key_id == key_id]
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
        "key_id": key_id,
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
    key_id: int | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(RequestLog).order_by(RequestLog.created_at.desc()).limit(limit)
    if key_id:
        stmt = stmt.where(RequestLog.virtual_key_id == key_id)

    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "request_id": r.request_id,
            "virtual_key_id": r.virtual_key_id,
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


@router.get("/admin/keys", dependencies=[Depends(require_admin_jwt)])
async def list_keys(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(VirtualKey).order_by(VirtualKey.created_at))).scalars().all()
    return [
        {
            "id": k.id,
            "team": k.team,
            "key_prefix": k.key_prefix,
            "org_id": k.org_id,
            "status": k.status,
            "requests_per_minute": k.requests_per_minute,
            "monthly_budget_usd": float(k.monthly_budget_usd),
            "created_at": k.created_at.isoformat(),
        }
        for k in rows
    ]


@router.get("/admin/cache/stats", dependencies=[Depends(require_admin_jwt)])
async def cache_stats(key_id: int | None = None, db: AsyncSession = Depends(get_db)):
    conditions = [RequestLog.status == "ok"]
    if key_id:
        conditions.append(RequestLog.virtual_key_id == key_id)

    stmt = select(
        func.count(RequestLog.id).filter(RequestLog.cache == "hit").label("hits"),
        func.count(RequestLog.id).filter(RequestLog.cache == "miss").label("misses"),
    ).where(*conditions)

    row = (await db.execute(stmt)).one()
    total = row.hits + row.misses
    return {
        "key_id": key_id,
        "hits": row.hits,
        "misses": row.misses,
        "hit_rate": round(row.hits / total, 4) if total else 0.0,
    }
