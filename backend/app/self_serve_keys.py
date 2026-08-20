import secrets

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import GatewayError, hash_virtual_key
from app.config import get_settings, known_catalog_names
from app.models import VirtualKey


def generate_virtual_key() -> str:
    return f"prism-sk-{secrets.token_urlsafe(16)}"


def _validate_model_allowlist(model_allowlist: list[str] | None) -> list[str]:
    """Full access is the default per the confirmed decision - there's no
    reserved/premium tier. That means every alias (fast/smart/auto/groq/
    gemini/...) AND every literal model name any provider registers
    (config/gateway_config.json's providers[].models[]) - not just the
    alias's single hardcoded primary. A BYOK provider often serves more
    models than the one alias picked as a default (e.g. groq's alias points
    at one model, but a tenant with their own Groq key can address any
    model Groq hosts by passing its exact name in "model", same as calling
    Groq directly) - restricting the default to aliases-only would make
    that literal-name addressing dead on arrival for every new key.

    Still validate any explicit list names only real aliases/models, so a
    self-serve key can't be created pointing at something that will 404 on
    every request."""
    known = known_catalog_names()
    if not model_allowlist:
        return sorted(known)
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

    # Only active keys count against the cap - otherwise disabling your only
    # key permanently locks you out of ever creating another one, since the
    # disabled row would count against the limit forever with no way to
    # delete it (there's no DELETE /me/keys endpoint, by design - disabling
    # is meant to be the retire-and-replace path).
    existing = await db.execute(
        select(func.count(VirtualKey.id)).where(
            VirtualKey.org_id == org_id, VirtualKey.status == "active"
        )
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
        # Required alongside cache_enabled - find_cache_hit() treats a null
        # threshold as "no basis for a match" and always misses (app/cache.py),
        # so leaving this unset would silently make caching a no-op that still
        # pays the cost of writing every response to cache_entries. 0.85
        # matches the seeded free-tier demo key's threshold.
        cache_similarity_threshold=0.85,
        status="active",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row, raw_key
