"""RS256 key pair for signing admin JWTs. The private key signs (login/refresh
issuance); the public key verifies (every authenticated admin request) - only
the code that mints tokens ever needs the private key.

Generated on first startup if missing, then reused - see docker-compose.yml's
`jwt_keys` volume for why this matters in Docker: without persisting the keys
directory, every container recreate would mint a new key pair and silently
invalidate every token issued before the restart."""

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import get_settings

_private_key_cache: str | None = None
_public_key_cache: str | None = None


def ensure_keys_exist() -> None:
    settings = get_settings()
    private_path = Path(settings.jwt_private_key_path)
    public_path = Path(settings.jwt_public_key_path)

    if private_path.exists() and public_path.exists():
        return

    private_path.parent.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    private_path.write_bytes(private_pem)
    private_path.chmod(0o600)
    public_path.write_bytes(public_pem)


def load_private_key() -> str:
    global _private_key_cache
    if _private_key_cache is None:
        _private_key_cache = Path(get_settings().jwt_private_key_path).read_text()
    return _private_key_cache


def load_public_key() -> str:
    global _public_key_cache
    if _public_key_cache is None:
        _public_key_cache = Path(get_settings().jwt_public_key_path).read_text()
    return _public_key_cache
