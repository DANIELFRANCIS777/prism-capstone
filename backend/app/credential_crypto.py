"""Symmetric encryption for tenant-submitted BYOK provider API keys
(app/provider_credentials.py). Unlike virtual-key hashing (app/auth.py,
one-way - the gateway only ever needs to *compare* a presented token), a
BYOK credential must be recoverable: the gateway needs the plaintext to
authenticate outbound calls to the tenant's own provider on their behalf. So
this is reversible Fernet encryption, not a hash.

Mirrors app/jwt_keys.py's generate-if-missing/persist/cache shape exactly.
The key lands in backend/keys/, already Docker-volume-persisted
(docker-compose.yml's `jwt_keys` volume) - without that, every container
recreate would mint a new key and silently make every stored credential
undecryptable."""

from pathlib import Path

from cryptography.fernet import Fernet

from app.config import get_settings

_fernet_key_cache: bytes | None = None


def ensure_fernet_key_exists() -> None:
    path = Path(get_settings().credential_encryption_key_path)
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(Fernet.generate_key())
    path.chmod(0o600)


def load_fernet_key() -> bytes:
    global _fernet_key_cache
    if _fernet_key_cache is None:
        _fernet_key_cache = Path(get_settings().credential_encryption_key_path).read_bytes()
    return _fernet_key_cache


def encrypt_api_key(plaintext: str) -> str:
    return Fernet(load_fernet_key()).encrypt(plaintext.encode()).decode()


def decrypt_api_key(ciphertext: str) -> str:
    return Fernet(load_fernet_key()).decrypt(ciphertext.encode()).decode()


def display_prefix(plaintext: str) -> str:
    return plaintext[:8] + "..." if len(plaintext) > 8 else plaintext
