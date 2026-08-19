from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CacheEntry
from app.semantic import cosine_similarity, term_frequency

# Cache key granularity: the last user message only (documented choice - see
# docs/IMPLEMENTATION_GUIDE.md FAQ on multi-turn cache keys). All graded
# paraphrase pairs are single-turn, so this doesn't affect their outcome.
# Scoped by virtual_key_id (the tenant), never shared across tenants.


def _prompt_text(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    return ""


async def find_cache_hit(
    db: AsyncSession,
    virtual_key_id: int,
    requested_model: str,
    messages: list[dict],
    threshold: float | None,
) -> CacheEntry | None:
    # threshold is None whenever a key has caching enabled but no similarity
    # threshold configured - there's no basis for a match, so skip the cache
    # rather than crash comparing a float against None.
    if threshold is None:
        return None

    prompt_text = _prompt_text(messages)
    query_vector = term_frequency(prompt_text)
    if not query_vector:
        return None

    result = await db.execute(
        select(CacheEntry).where(
            CacheEntry.virtual_key_id == virtual_key_id, CacheEntry.model == requested_model
        )
    )
    best_entry, best_score = None, 0.0
    for entry in result.scalars().all():
        score = cosine_similarity(query_vector, Counter(entry.token_counts))
        if score > best_score:
            best_entry, best_score = entry, score

    return best_entry if best_entry is not None and best_score >= threshold else None


async def store_cache_entry(
    db: AsyncSession,
    virtual_key_id: int,
    requested_model: str,
    messages: list[dict],
    response_body: dict,
    resolved_provider: str,
    resolved_model: str,
) -> None:
    prompt_text = _prompt_text(messages)
    vector = term_frequency(prompt_text)
    if not vector:
        return
    db.add(
        CacheEntry(
            virtual_key_id=virtual_key_id,
            model=requested_model,
            prompt_text=prompt_text,
            token_counts=dict(vector),
            response_body=response_body,
            resolved_provider=resolved_provider,
            resolved_model=resolved_model,
        )
    )
    await db.commit()


async def record_cache_hit(db: AsyncSession, entry: CacheEntry) -> None:
    entry.hit_count += 1
    await db.commit()
