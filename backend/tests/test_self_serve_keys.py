import pytest

from app.auth import GatewayError
from app.config import get_settings
from app.models import Organization
from app.self_serve_keys import create_key


async def _make_org(db) -> int:
    org = Organization(name="test org")
    db.add(org)
    await db.commit()
    await db.refresh(org)
    return org.id


async def test_create_key_within_ceiling_succeeds(db):
    org_id = await _make_org(db)
    row, raw_key = await create_key(
        db, org_id=org_id, label=None, requests_per_minute=10, monthly_budget_usd=5
    )
    assert row.org_id == org_id
    assert raw_key.startswith("prism-sk-")
    assert row.key_prefix == raw_key[:12]


async def test_create_key_above_rpm_ceiling_is_rejected(db):
    org_id = await _make_org(db)
    settings = get_settings()
    with pytest.raises(GatewayError) as exc_info:
        await create_key(
            db,
            org_id=org_id,
            label=None,
            requests_per_minute=settings.self_serve_max_requests_per_minute + 1,
            monthly_budget_usd=1,
        )
    assert exc_info.value.status_code == 422


async def test_create_key_above_budget_ceiling_is_rejected(db):
    org_id = await _make_org(db)
    settings = get_settings()
    with pytest.raises(GatewayError) as exc_info:
        await create_key(
            db,
            org_id=org_id,
            label=None,
            requests_per_minute=1,
            monthly_budget_usd=settings.self_serve_max_monthly_budget_usd + 1,
        )
    assert exc_info.value.status_code == 422


async def test_second_key_for_same_org_is_rejected_by_the_per_org_cap(db):
    org_id = await _make_org(db)
    await create_key(db, org_id=org_id, label=None, requests_per_minute=1, monthly_budget_usd=1)

    with pytest.raises(GatewayError) as exc_info:
        await create_key(
            db, org_id=org_id, label=None, requests_per_minute=1, monthly_budget_usd=1
        )
    assert exc_info.value.status_code == 422


async def test_disabling_a_key_frees_the_cap_for_a_replacement(db):
    """Regression test: the cap used to count every key ever created,
    active or not - disabling your only key permanently locked you out of
    ever creating another one, since there's no way to delete a key
    outright. Disabling must free the slot."""
    org_id = await _make_org(db)
    first, _ = await create_key(
        db, org_id=org_id, label=None, requests_per_minute=1, monthly_budget_usd=1
    )
    first.status = "disabled"
    await db.commit()

    second, _ = await create_key(
        db, org_id=org_id, label=None, requests_per_minute=1, monthly_budget_usd=1
    )
    assert second.id != first.id
    assert second.status == "active"


async def test_unknown_model_in_allowlist_is_rejected(db):
    org_id = await _make_org(db)
    with pytest.raises(GatewayError) as exc_info:
        await create_key(
            db,
            org_id=org_id,
            label=None,
            requests_per_minute=1,
            monthly_budget_usd=1,
            model_allowlist=["not-a-real-model"],
        )
    assert exc_info.value.status_code == 422


async def test_default_allowlist_is_every_configured_alias_and_model(db):
    """Default access is every alias AND every literal model name any
    provider registers - not just each alias's one hardcoded primary. A
    tenant with their own BYOK key can address any model that provider
    hosts by exact name, the same as calling that provider directly."""
    org_id = await _make_org(db)
    row, _ = await create_key(
        db, org_id=org_id, label=None, requests_per_minute=1, monthly_budget_usd=1
    )
    assert {"fast", "smart", "auto", "groq", "gemini"} <= set(row.model_allowlist)
    assert "openai/gpt-oss-120b" in row.model_allowlist
    assert "gemini-2.5-flash-lite" in row.model_allowlist


async def test_cache_enabled_key_gets_a_usable_similarity_threshold(db):
    """Regression test: cache_enabled=True with no threshold is not 'caching
    off', it's caching that silently never hits - find_cache_hit() treats a
    null threshold as no basis for a match and always misses (app/cache.py),
    while chat.py still writes a cache_entries row on every response. A
    self-serve key must never end up in that state."""
    org_id = await _make_org(db)
    row, _ = await create_key(
        db, org_id=org_id, label=None, requests_per_minute=1, monthly_budget_usd=1
    )
    assert row.cache_enabled is True
    assert row.cache_similarity_threshold is not None
