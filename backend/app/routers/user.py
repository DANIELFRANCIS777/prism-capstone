from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import GatewayError
from app.auth_throttle import (
    clear_auth_failures,
    client_identifier,
    enforce_auth_rate_limit,
    enforce_not_locked_out,
    record_auth_failure,
)
from app.config import get_settings, load_gateway_config
from app.db import get_db
from app.models import Organization, RequestLog, User, VirtualKey
from app.provider_credentials import (
    delete_credential,
    list_credentials,
    upsert_credential,
)
from app.self_serve_keys import create_key
from app.user_auth import (
    authenticate_user,
    hash_password,
    issue_token_pair,
    require_user_jwt,
    revoke_refresh_token,
    rotate_refresh_token,
)

router = APIRouter()


class SignupRequest(BaseModel):
    email: str
    password: str
    org_name: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class CredentialRequest(BaseModel):
    api_key: str
    label: str | None = None


class CreateKeyRequest(BaseModel):
    label: str | None = None
    requests_per_minute: int
    monthly_budget_usd: float
    model_allowlist: list[str] | None = None


class UpdateKeyRequest(BaseModel):
    status: str


@router.get("/auth/config")
async def auth_config():
    settings = get_settings()
    return {
        "signup_enabled": settings.self_serve_signup_enabled,
        "max_requests_per_minute": settings.self_serve_max_requests_per_minute,
        "max_monthly_budget_usd": settings.self_serve_max_monthly_budget_usd,
        "max_keys_per_org": settings.self_serve_max_keys_per_org,
    }


@router.post("/auth/signup")
async def signup(request: Request, body: SignupRequest, db: AsyncSession = Depends(get_db)):
    settings = get_settings()
    if not settings.self_serve_signup_enabled:
        raise GatewayError(403, "Signup is disabled on this instance", "signup_disabled")

    await enforce_auth_rate_limit(db, "signup", client_identifier(request))

    existing = await db.execute(select(User.id).where(User.email == body.email))
    if existing.scalar_one_or_none() is not None:
        raise GatewayError(409, "An account with this email already exists", "email_taken")

    org = Organization(name=body.org_name)
    db.add(org)
    await db.flush()

    user = User(
        email=body.email, password_hash=hash_password(body.password), org_id=org.id, role="owner"
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return await issue_token_pair(user, db)


@router.post("/auth/login")
async def login(request: Request, body: LoginRequest, db: AsyncSession = Depends(get_db)):
    # Throttle on both the caller's IP and the targeted email: the IP limit
    # stops one host spraying many accounts, the email limit stops a
    # distributed attempt on one account.
    email = body.email.strip().lower()
    await enforce_auth_rate_limit(db, "login", client_identifier(request))
    await enforce_auth_rate_limit(db, "login_email", email)
    await enforce_not_locked_out(db, email)

    try:
        user = await authenticate_user(body.email, body.password, db)
    except GatewayError:
        await record_auth_failure(db, email)
        raise

    await clear_auth_failures(db, email)
    return await issue_token_pair(user, db)


@router.post("/auth/refresh")
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    return await rotate_refresh_token(body.refresh_token, db)


@router.post("/auth/logout")
async def logout(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    await revoke_refresh_token(body.refresh_token, db)
    return {"status": "ok"}


@router.get("/me")
async def me(claims: dict = Depends(require_user_jwt), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == claims["user_id"]))
    user = result.scalar_one()
    org_result = await db.execute(select(Organization).where(Organization.id == user.org_id))
    org = org_result.scalar_one()
    return {
        "id": user.id,
        "email": user.email,
        "org": {"id": org.id, "name": org.name, "created_at": org.created_at.isoformat()},
    }


@router.get("/me/credentials")
async def get_credentials(claims: dict = Depends(require_user_jwt), db: AsyncSession = Depends(get_db)):
    rows = await list_credentials(db, claims["org_id"])
    return [
        {
            "provider": r.provider,
            "label": r.label,
            "key_prefix": r.key_prefix,
            "status": r.status,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.put("/me/credentials/{provider}")
async def put_credential(
    provider: str,
    body: CredentialRequest,
    claims: dict = Depends(require_user_jwt),
    db: AsyncSession = Depends(get_db),
):
    known_providers = {p["name"] for p in load_gateway_config()["providers"]}
    if provider not in known_providers:
        raise GatewayError(422, f"Unknown provider '{provider}'", "invalid_request_error")

    row = await upsert_credential(db, claims["org_id"], provider, body.api_key, body.label)
    return {
        "provider": row.provider,
        "label": row.label,
        "key_prefix": row.key_prefix,
        "status": row.status,
        "created_at": row.created_at.isoformat(),
    }


@router.delete("/me/credentials/{provider}", status_code=204)
async def remove_credential(
    provider: str, claims: dict = Depends(require_user_jwt), db: AsyncSession = Depends(get_db)
):
    await delete_credential(db, claims["org_id"], provider)


@router.post("/me/keys")
async def issue_key(
    body: CreateKeyRequest, claims: dict = Depends(require_user_jwt), db: AsyncSession = Depends(get_db)
):
    row, raw_key = await create_key(
        db,
        org_id=claims["org_id"],
        label=body.label,
        requests_per_minute=body.requests_per_minute,
        monthly_budget_usd=body.monthly_budget_usd,
        model_allowlist=body.model_allowlist,
    )
    return {
        "id": row.id,
        "virtual_key": raw_key,
        "label": row.team,
        "requests_per_minute": row.requests_per_minute,
        "monthly_budget_usd": float(row.monthly_budget_usd),
        "model_allowlist": row.model_allowlist,
        "status": row.status,
        "created_at": row.created_at.isoformat(),
    }


@router.get("/me/keys")
async def list_keys(claims: dict = Depends(require_user_jwt), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(VirtualKey).where(VirtualKey.org_id == claims["org_id"]))
    return [
        {
            "id": k.id,
            "label": k.team,
            "key_prefix": k.key_prefix,
            "requests_per_minute": k.requests_per_minute,
            "monthly_budget_usd": float(k.monthly_budget_usd),
            "status": k.status,
            "created_at": k.created_at.isoformat(),
        }
        for k in result.scalars().all()
    ]


@router.patch("/me/keys/{key_id}")
async def update_key(
    key_id: int,
    body: UpdateKeyRequest,
    claims: dict = Depends(require_user_jwt),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(VirtualKey).where(VirtualKey.id == key_id, VirtualKey.org_id == claims["org_id"])
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise GatewayError(404, "Key not found", "not_found_error")
    row.status = body.status
    await db.commit()
    await db.refresh(row)
    return {
        "id": row.id,
        "label": row.team,
        "key_prefix": row.key_prefix,
        "requests_per_minute": row.requests_per_minute,
        "monthly_budget_usd": float(row.monthly_budget_usd),
        "status": row.status,
        "created_at": row.created_at.isoformat(),
    }


def _org_key_ids(org_id: int):
    return select(VirtualKey.id).where(VirtualKey.org_id == org_id)


@router.get("/me/usage")
async def usage_summary(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    claims: dict = Depends(require_user_jwt),
    db: AsyncSession = Depends(get_db),
):
    conditions = [RequestLog.virtual_key_id.in_(_org_key_ids(claims["org_id"]))]
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
        "requests": row.requests,
        "prompt_tokens": row.prompt_tokens,
        "completion_tokens": row.completion_tokens,
        "cost_usd": float(row.cost_usd),
        "cache_hits": row.cache_hits,
    }


@router.get("/me/logs")
async def recent_logs(
    limit: int = 50, claims: dict = Depends(require_user_jwt), db: AsyncSession = Depends(get_db)
):
    stmt = (
        select(RequestLog)
        .where(RequestLog.virtual_key_id.in_(_org_key_ids(claims["org_id"])))
        .order_by(RequestLog.created_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "request_id": r.request_id,
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


@router.get("/me/cache-stats")
async def cache_stats(claims: dict = Depends(require_user_jwt), db: AsyncSession = Depends(get_db)):
    conditions = [
        RequestLog.status == "ok",
        RequestLog.virtual_key_id.in_(_org_key_ids(claims["org_id"])),
    ]
    stmt = select(
        func.count(RequestLog.id).filter(RequestLog.cache == "hit").label("hits"),
        func.count(RequestLog.id).filter(RequestLog.cache == "miss").label("misses"),
    ).where(*conditions)

    row = (await db.execute(stmt)).one()
    total = row.hits + row.misses
    return {
        "hits": row.hits,
        "misses": row.misses,
        "hit_rate": round(row.hits / total, 4) if total else 0.0,
    }
