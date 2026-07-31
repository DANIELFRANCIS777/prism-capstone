import uuid

from app.cache import find_cache_hit, store_cache_entry


async def test_cache_hits_paraphrase_and_misses_unrelated_prompt(db):
    key = f"test-cache-{uuid.uuid4().hex[:8]}"
    original = [{"role": "user", "content": "How do I reset my password on the dashboard?"}]
    paraphrase = [{"role": "user", "content": "What are the steps to reset my dashboard password?"}]
    unrelated = [{"role": "user", "content": "Compare TCP and UDP for game servers."}]

    await store_cache_entry(db, key, "fast", original, {"id": "resp1"}, "alpha", "alpha-small")

    hit = await find_cache_hit(db, key, "fast", paraphrase, threshold=0.85)
    assert hit is not None
    assert hit.response_body == {"id": "resp1"}

    miss = await find_cache_hit(db, key, "fast", unrelated, threshold=0.85)
    assert miss is None


async def test_cache_is_never_shared_across_virtual_keys(db):
    """The Must Have contract: a cache hit that returns another team's
    response is a data leak. This is the direct automated check for it."""
    key_a = f"test-cache-a-{uuid.uuid4().hex[:8]}"
    key_b = f"test-cache-b-{uuid.uuid4().hex[:8]}"
    messages = [{"role": "user", "content": "a distinctive cache isolation probe prompt"}]

    await store_cache_entry(db, key_a, "fast", messages, {"id": "secret"}, "alpha", "alpha-small")

    hit_for_owner = await find_cache_hit(db, key_a, "fast", messages, threshold=0.5)
    assert hit_for_owner is not None

    hit_for_other_key = await find_cache_hit(db, key_b, "fast", messages, threshold=0.5)
    assert hit_for_other_key is None


async def test_cache_is_scoped_per_alias_not_just_per_key(db):
    key = f"test-cache-alias-{uuid.uuid4().hex[:8]}"
    messages = [{"role": "user", "content": "a distinctive per-alias cache probe prompt"}]

    await store_cache_entry(db, key, "fast", messages, {"id": "fast-resp"}, "alpha", "alpha-small")

    hit_same_alias = await find_cache_hit(db, key, "fast", messages, threshold=0.5)
    assert hit_same_alias is not None

    hit_different_alias = await find_cache_hit(db, key, "smart", messages, threshold=0.5)
    assert hit_different_alias is None
