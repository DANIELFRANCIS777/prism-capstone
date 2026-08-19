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


async def test_default_allowlist_is_every_configured_alias(db):
    org_id = await _make_org(db)
    row, _ = await create_key(
        db, org_id=org_id, label=None, requests_per_minute=1, monthly_budget_usd=1
    )
    assert set(row.model_allowlist) == {"fast", "smart", "auto"}
