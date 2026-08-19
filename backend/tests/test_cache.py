import uuid

from app.cache import find_cache_hit, store_cache_entry
from tests.conftest import make_virtual_key


async def _persisted_key(db, **overrides) -> int:
    key = make_virtual_key(raw_key=f"test-cache-{uuid.uuid4().hex[:8]}", **overrides)
    db.add(key)
    await db.commit()
    return key.id


async def test_cache_hits_paraphrase_and_misses_unrelated_prompt(db):
    key_id = await _persisted_key(db)
    original = [{"role": "user", "content": "How do I reset my password on the dashboard?"}]
    paraphrase = [{"role": "user", "content": "What are the steps to reset my dashboard password?"}]
    unrelated = [{"role": "user", "content": "Compare TCP and UDP for game servers."}]

    await store_cache_entry(db, key_id, "fast", original, {"id": "resp1"}, "alpha", "alpha-small")

    hit = await find_cache_hit(db, key_id, "fast", paraphrase, threshold=0.85)
    assert hit is not None
    assert hit.response_body == {"id": "resp1"}

    miss = await find_cache_hit(db, key_id, "fast", unrelated, threshold=0.85)
    assert miss is None


async def test_cache_is_never_shared_across_virtual_keys(db):
    """The Must Have contract: a cache hit that returns another team's
    response is a data leak. This is the direct automated check for it."""
    key_a_id = await _persisted_key(db)
    key_b_id = await _persisted_key(db)
    messages = [{"role": "user", "content": "a distinctive cache isolation probe prompt"}]

    await store_cache_entry(db, key_a_id, "fast", messages, {"id": "secret"}, "alpha", "alpha-small")

    hit_for_owner = await find_cache_hit(db, key_a_id, "fast", messages, threshold=0.5)
    assert hit_for_owner is not None

    hit_for_other_key = await find_cache_hit(db, key_b_id, "fast", messages, threshold=0.5)
    assert hit_for_other_key is None


async def test_cache_is_scoped_per_alias_not_just_per_key(db):
    key_id = await _persisted_key(db)
    messages = [{"role": "user", "content": "a distinctive per-alias cache probe prompt"}]

    await store_cache_entry(db, key_id, "fast", messages, {"id": "fast-resp"}, "alpha", "alpha-small")

    hit_same_alias = await find_cache_hit(db, key_id, "fast", messages, threshold=0.5)
    assert hit_same_alias is not None

    hit_different_alias = await find_cache_hit(db, key_id, "smart", messages, threshold=0.5)
    assert hit_different_alias is None


async def test_cache_lookup_skips_rather_than_crashes_on_null_threshold(db):
    """Regression test: cache_similarity_threshold is nullable (a key can have
    caching enabled with no threshold configured). find_cache_hit must treat
    that as "no match possible", not raise comparing float >= None."""
    key_id = await _persisted_key(db)
    messages = [{"role": "user", "content": "does this crash without a threshold"}]

    await store_cache_entry(db, key_id, "fast", messages, {"id": "resp"}, "alpha", "alpha-small")

    result = await find_cache_hit(db, key_id, "fast", messages, threshold=None)
    assert result is None
