import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings, load_seed_keys
from app.models import AdminUser, VirtualKey


async def seed_virtual_keys(session: AsyncSession) -> None:
    """Upsert tenants from data/seed_keys.json. Preserves seed key values on rerun."""
    for tenant in load_seed_keys():
        result = await session.execute(
            select(VirtualKey).where(VirtualKey.virtual_key == tenant["virtual_key"])
        )
        row = result.scalar_one_or_none()
        cache_cfg = tenant["semantic_cache"]
        fields = dict(
            team=tenant["team"],
            monthly_budget_usd=tenant["monthly_budget_usd"],
            requests_per_minute=tenant["rate_limit"]["requests_per_minute"],
            tokens_per_minute=tenant["rate_limit"].get("tokens_per_minute"),
            model_allowlist=tenant["model_allowlist"],
            cache_enabled=cache_cfg["enabled"],
            cache_similarity_threshold=cache_cfg.get("similarity_threshold"),
        )
        if row is None:
            session.add(VirtualKey(virtual_key=tenant["virtual_key"], **fields))
        else:
            for key, value in fields.items():
                setattr(row, key, value)
    await session.commit()


async def seed_admin_user(session: AsyncSession) -> None:
    """Bootstraps exactly one admin account if admin_users is empty - there is
    no signup route. Additional admins currently have to be inserted directly
    into admin_users (a create-admin API is Good To Have, not built here)."""
    existing = await session.execute(select(AdminUser.id).limit(1))
    if existing.scalar_one_or_none() is not None:
        return

    settings = get_settings()
    password_hash = bcrypt.hashpw(
        settings.admin_bootstrap_password.encode(), bcrypt.gensalt()
    ).decode()
    session.add(
        AdminUser(username=settings.admin_bootstrap_username, password_hash=password_hash)
    )
    await session.commit()
