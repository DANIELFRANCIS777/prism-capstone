"""End-to-end tests through the real ASGI app.

Every other test in this suite calls internal functions directly, which
leaves the router wiring itself unproven by pytest - only scripts/
smoke_test.py covers that, and it needs a fully running stack. These use
httpx's ASGITransport (httpx is already a dependency) to exercise the app
in-process: real routing, real dependency injection, real status codes and
response bodies, no server or network needed.
"""

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import get_db
from app.main import app
from tests.conftest import make_virtual_key


@pytest_asyncio.fixture
async def client(test_engine, db):
    """The real app, with get_db overridden onto the test session so requests
    see the same transaction the test sets up. Startup/shutdown (lifespan) is
    deliberately not run - migrations and seeding are the test fixtures' job,
    and running them per-test would be slow and redundant."""
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def test_models_requires_authentication(client):
    response = await client.get("/v1/models")
    assert response.status_code == 401
    assert response.json()["error"]["type"] == "authentication_error"


async def test_models_rejects_an_unknown_key(client):
    response = await client.get(
        "/v1/models", headers={"Authorization": "Bearer not-a-real-key"}
    )
    assert response.status_code == 401


async def test_models_lists_only_what_the_key_may_call(client, db):
    """The scoping that makes a separate provider-validation step
    unnecessary: a key restricted to one model never sees the others."""
    key = make_virtual_key(model_allowlist=["fast"])
    db.add(key)
    await db.commit()

    response = await client.get(
        "/v1/models", headers={"Authorization": f"Bearer {key.raw_key}"}
    )
    assert response.status_code == 200

    body = response.json()
    assert body["object"] == "list"
    assert [m["id"] for m in body["data"]] == ["fast"]


async def test_models_lists_every_allowed_entry_for_a_broad_key(client, db):
    key = make_virtual_key(model_allowlist=["fast", "openai/gpt-oss-20b", "gemini-2.5-flash"])
    db.add(key)
    await db.commit()

    response = await client.get(
        "/v1/models", headers={"Authorization": f"Bearer {key.raw_key}"}
    )
    ids = [m["id"] for m in response.json()["data"]]
    assert ids == ["fast", "gemini-2.5-flash", "openai/gpt-oss-20b"]


async def test_health_is_unauthenticated_and_ok(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_ready_reports_database_connectivity(client):
    """Unlike /health (pure liveness), /ready proves the dependency a request
    actually needs is reachable."""
    response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json()["database"] == "ok"
