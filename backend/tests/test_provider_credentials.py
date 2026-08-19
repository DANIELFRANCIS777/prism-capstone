from app.credential_crypto import decrypt_api_key, ensure_fernet_key_exists, encrypt_api_key
from app.models import Organization
from app.provider_credentials import (
    delete_credential,
    list_credentials,
    load_org_provider_credentials,
    upsert_credential,
)

ensure_fernet_key_exists()


async def _make_org(db) -> int:
    org = Organization(name="test org")
    db.add(org)
    await db.commit()
    await db.refresh(org)
    return org.id


def test_encrypt_decrypt_round_trips():
    plaintext = "sk-super-secret-provider-key"
    ciphertext = encrypt_api_key(plaintext)
    assert ciphertext != plaintext
    assert decrypt_api_key(ciphertext) == plaintext


async def test_stored_credential_never_exposes_plaintext(db):
    org_id = await _make_org(db)
    await upsert_credential(db, org_id, "alpha", "sk-real-secret-value", label="prod")

    rows = await list_credentials(db, org_id)
    assert len(rows) == 1
    assert "sk-real-secret-value" not in rows[0].encrypted_key
    assert rows[0].key_prefix == "sk-real-..."


async def test_upsert_overwrites_existing_credential_for_same_provider(db):
    org_id = await _make_org(db)
    await upsert_credential(db, org_id, "alpha", "sk-old-value", label="old")
    await upsert_credential(db, org_id, "alpha", "sk-new-value", label="new")

    rows = await list_credentials(db, org_id)
    assert len(rows) == 1
    assert rows[0].label == "new"

    decrypted = await load_org_provider_credentials(db, org_id)
    assert decrypted["alpha"] == "sk-new-value"


async def test_delete_credential_removes_it(db):
    org_id = await _make_org(db)
    await upsert_credential(db, org_id, "alpha", "sk-to-delete", label=None)
    await delete_credential(db, org_id, "alpha")

    rows = await list_credentials(db, org_id)
    assert rows == []


async def test_load_org_provider_credentials_is_scoped_per_org(db):
    org_a = await _make_org(db)
    org_b = await _make_org(db)
    await upsert_credential(db, org_a, "alpha", "sk-org-a-key", label=None)

    creds_a = await load_org_provider_credentials(db, org_a)
    creds_b = await load_org_provider_credentials(db, org_b)
    assert creds_a == {"alpha": "sk-org-a-key"}
    assert creds_b == {}
