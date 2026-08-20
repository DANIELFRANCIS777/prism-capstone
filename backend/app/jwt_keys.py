"""RS256 key pair for signing admin and tenant JWTs. The private key signs
(login/refresh issuance); the public key verifies (every authenticated
request) - only the code that mints tokens ever needs the private key.

Key material resolves in this order, mirroring app/credential_crypto.py:

1. JWT_PRIVATE_KEY / JWT_PUBLIC_KEY (env vars / platform secrets), holding
   the PEM text itself. Required on any host with an ephemeral filesystem,
   where a generated file does not survive a redeploy.
2. Files at JWT_PRIVATE_KEY_PATH / JWT_PUBLIC_KEY_PATH, generated on first
   startup if missing - local dev and the Compose stack, where keys/ is
   volume-persisted.

Losing this pair is less severe than losing the credential encryption key -
it invalidates every issued token, so everyone is logged out and has to sign
in again - but on a platform that regenerates it every deploy, sessions
would silently break on each release.
"""

import logging
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import get_settings

logger = logging.getLogger(__name__)

_private_key_cache: str | None = None
_public_key_cache: str | None = None


def generate_key_pair() -> tuple[str, str]:
    """Returns (private_pem, public_pem) as text, for an operator to paste
    into their platform's secrets. See `python -m app.keygen`."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


def ensure_keys_exist() -> None:
    """No-op when the pair is supplied by env - there's nothing to create."""
    settings = get_settings()
    if settings.jwt_private_key and settings.jwt_public_key:
        return

    private_path = Path(settings.jwt_private_key_path)
    public_path = Path(settings.jwt_public_key_path)
    if private_path.exists() and public_path.exists():
        return

    private_path.parent.mkdir(parents=True, exist_ok=True)
    private_pem, public_pem = generate_key_pair()

    private_path.write_text(private_pem)
    private_path.chmod(0o600)
    public_path.write_text(public_pem)
    logger.warning(
        "generated a new JWT signing key pair; any previously issued token is now invalid",
        extra={"path": str(private_path)},
    )


def _normalize_pem(pem: str) -> str:
    """Accept a PEM whose newlines arrived escaped as a literal backslash-n.

    Platforms differ on multi-line secret values: some (Fly, k8s) preserve
    real newlines, others (a few CI UIs and .env parsers) only reliably
    round-trip a single line. Tolerating both means an operator can paste
    whichever form their platform gives them without the failure mode being
    an opaque 'could not deserialize key data' at startup."""
    return pem.replace("\\n", "\n").strip() + "\n"


def load_private_key() -> str:
    global _private_key_cache
    if _private_key_cache is None:
        settings = get_settings()
        if settings.jwt_private_key:
            _private_key_cache = _normalize_pem(settings.jwt_private_key)
        else:
            _private_key_cache = Path(settings.jwt_private_key_path).read_text()
    return _private_key_cache


def load_public_key() -> str:
    global _public_key_cache
    if _public_key_cache is None:
        settings = get_settings()
        if settings.jwt_public_key:
            _public_key_cache = _normalize_pem(settings.jwt_public_key)
        else:
            _public_key_cache = Path(settings.jwt_public_key_path).read_text()
    return _public_key_cache
