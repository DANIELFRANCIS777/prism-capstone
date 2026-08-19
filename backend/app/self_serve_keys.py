import secrets

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import GatewayError, hash_virtual_key
from app.config import get_settings, load_gateway_config, model_provider_map
from app.models import VirtualKey


def generate_virtual_key() -> str:
    return f"prism-sk-{secrets.token_urlsafe(16)}"


def _validate_model_allowlist(model_allowlist: list[str] | None) -> list[str]:
    """Full access (every alias) is the default per the confirmed decision -
    there's no reserved/premium tier. Still validate any explicit list names
    only real aliases/models, so a self-serve key can't be created pointing
    at something that will 404 on every request."""
    if not model_allowlist:
        return list(load_gateway_config()["model_aliases"].keys())
    known = set(load_gateway_config()["model_aliases"]) | set(model_provider_map())
    unknown = [m for m in model_allowlist if m not in known]
    if unknown:
        raise GatewayError(
            422, f"Unknown model/alias in model_allowlist: {unknown}", "invalid_request_error"
        )
    return model_allowlist


async def create_key(
    db: AsyncSession,
    org_id: int,
    label: str | None,
    requests_per_minute: int,
    monthly_budget_usd: float,
    model_allowlist: list[str] | None = None,
) -> tuple[VirtualKey, str]:
    """Validates the requested limits against the operator's ceiling and the
    one-key-per-org cap (Settings.self_serve_max_keys_per_org), then issues a
    new key. Returns (row, raw_key) - raw_key is shown to the caller exactly
    once here; only its hash is ever persisted (app/auth.py)."""
    settings = get_settings()

    if requests_per_minute > settings.self_serve_max_requests_per_minute:
        raise GatewayError(
            422,
            f"requests_per_minute may not exceed {settings.self_serve_max_requests_per_minute}",
            "invalid_request_error",
        )
    if monthly_budget_usd > settings.self_serve_max_monthly_budget_usd:
        raise GatewayError(
            422,
            f"monthly_budget_usd may not exceed {settings.self_serve_max_monthly_budget_usd}",
            "invalid_request_error",
        )

    existing = await db.execute(
        select(func.count(VirtualKey.id)).where(VirtualKey.org_id == org_id)
    )
    if existing.scalar_one() >= settings.self_serve_max_keys_per_org:
        raise GatewayError(
            422,
            f"This account already has the maximum of "
            f"{settings.self_serve_max_keys_per_org} key(s)",
            "invalid_request_error",
        )

    allowlist = _validate_model_allowlist(model_allowlist)
    raw_key = generate_virtual_key()
    row = VirtualKey(
        key_hash=hash_virtual_key(raw_key),
        key_prefix=raw_key[:12],
        team=label or "self-serve",
        org_id=org_id,
        monthly_budget_usd=monthly_budget_usd,
        requests_per_minute=requests_per_minute,
        model_allowlist=allowlist,
        cache_enabled=True,
        status="active",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row, raw_key
