from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credential_crypto import decrypt_api_key, display_prefix, encrypt_api_key
from app.models import ProviderCredential


async def upsert_credential(
    db: AsyncSession, org_id: int, provider: str, plaintext_key: str, label: str | None
) -> ProviderCredential:
    result = await db.execute(
        select(ProviderCredential).where(
            ProviderCredential.org_id == org_id, ProviderCredential.provider == provider
        )
    )
    row = result.scalar_one_or_none()
    encrypted = encrypt_api_key(plaintext_key)
    prefix = display_prefix(plaintext_key)
    if row is None:
        row = ProviderCredential(
            org_id=org_id,
            provider=provider,
            encrypted_key=encrypted,
            key_prefix=prefix,
            label=label,
        )
        db.add(row)
    else:
        row.encrypted_key = encrypted
        row.key_prefix = prefix
        row.label = label
        row.status = "active"
    await db.commit()
    await db.refresh(row)
    return row


async def list_credentials(db: AsyncSession, org_id: int) -> list[ProviderCredential]:
    result = await db.execute(
        select(ProviderCredential).where(ProviderCredential.org_id == org_id)
    )
    return list(result.scalars().all())


async def delete_credential(db: AsyncSession, org_id: int, provider: str) -> None:
    result = await db.execute(
        select(ProviderCredential).where(
            ProviderCredential.org_id == org_id, ProviderCredential.provider == provider
        )
    )
    row = result.scalar_one_or_none()
    if row is not None:
        await db.delete(row)
        await db.commit()


async def load_org_provider_credentials(db: AsyncSession, org_id: int) -> dict[str, str]:
    """{provider_name: decrypted_plaintext} for this org's active credentials.
    Called once per chat.py request when the key has an org - see
    app/routing/dispatch.py's provider_credentials override."""
    result = await db.execute(
        select(ProviderCredential).where(
            ProviderCredential.org_id == org_id, ProviderCredential.status == "active"
        )
    )
    return {row.provider: decrypt_api_key(row.encrypted_key) for row in result.scalars().all()}
