from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import GatewayError
from app.models import UsageRecord, VirtualKey

# Calendar-month window, reset-on-read (no proration). Documented simplification
# per docs/DATA_MODEL.md.


def current_year_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


async def enforce_budget(db: AsyncSession, key: VirtualKey, year_month: str) -> None:
    """Admission check only: reject if the budget is already fully spent. The
    actual cost of *this* request isn't known until the upstream call returns,
    so concurrent in-flight requests admitted while budget remained may push
    spend slightly over - documented per docs/DATA_MODEL.md."""
    result = await db.execute(
        select(UsageRecord.spend_usd).where(
            UsageRecord.virtual_key_id == key.id, UsageRecord.year_month == year_month
        )
    )
    spend = result.scalar_one_or_none() or 0
    if float(spend) >= float(key.monthly_budget_usd):
        raise GatewayError(
            402,
            f"Monthly budget of ${key.monthly_budget_usd} exhausted for this key",
            "budget_exceeded",
        )


async def record_usage(
    db: AsyncSession,
    key: VirtualKey,
    year_month: str,
    cost_usd: float,
    prompt_tokens: int,
    completion_tokens: int,
    cache_hit: bool = False,
) -> None:
    """Atomic upsert-increment - two concurrent requests both get counted."""
    stmt = pg_insert(UsageRecord).values(
        virtual_key_id=key.id,
        year_month=year_month,
        spend_usd=cost_usd,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        requests=1,
        cache_hits=1 if cache_hit else 0,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[UsageRecord.virtual_key_id, UsageRecord.year_month],
        set_={
            "spend_usd": UsageRecord.spend_usd + stmt.excluded.spend_usd,
            "prompt_tokens": UsageRecord.prompt_tokens + stmt.excluded.prompt_tokens,
            "completion_tokens": UsageRecord.completion_tokens + stmt.excluded.completion_tokens,
            "requests": UsageRecord.requests + stmt.excluded.requests,
            "cache_hits": UsageRecord.cache_hits + stmt.excluded.cache_hits,
        },
    )
    await db.execute(stmt)
    await db.commit()
