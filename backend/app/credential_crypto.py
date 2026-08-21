"""Symmetric encryption for tenant-submitted BYOK provider API keys
(app/provider_credentials.py). Unlike virtual-key hashing (app/auth.py,
one-way - the gateway only ever needs to *compare* a presented token), a
BYOK credential must be recoverable: the gateway needs the plaintext to
authenticate outbound calls to the tenant's own provider on their behalf. So
this is reversible Fernet encryption, not a hash.

Key material resolves in this order:

1. CREDENTIAL_ENCRYPTION_KEY (an env var / platform secret). This is the
   only correct option on a host with an ephemeral filesystem - Render,
   Cloud Run, Fly without a volume - where a generated file does not
   survive a redeploy.
2. A file at CREDENTIAL_ENCRYPTION_KEY_PATH, generated on first startup if
   missing. Convenient for local dev and the Docker Compose stack, where
   the keys/ directory is volume-persisted.

Losing this key is not a recoverable outage: every provider_credentials row
becomes permanently undecryptable ciphertext, and every tenant has to
re-enter their provider API key. app/config.py::validate_production_settings
refuses to start a production deployment that would depend on the
generate-a-file path for exactly that reason.
"""

import logging
from pathlib import Path

from cryptography.fernet import Fernet

from app.config import get_settings
from app.jwt_keys import assert_readable

logger = logging.getLogger(__name__)

_fernet_key_cache: bytes | None = None


def ensure_fernet_key_exists() -> None:
    """No-op when the key is supplied by env - there's nothing to create."""
    settings = get_settings()
    if settings.credential_encryption_key:
        return

    path = Path(settings.credential_encryption_key_path)
    if path.exists():
        # Same failure mode as the JWT keys: a file left by a root-era
        # container in a volume the unprivileged user can't read.
        assert_readable(path, "CREDENTIAL_ENCRYPTION_KEY")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(Fernet.generate_key())
    path.chmod(0o600)
    logger.warning(
        "generated a new credential encryption key",
        extra={
            "path": str(path),
            "note": "existing encrypted credentials are only readable with the original key",
        },
    )


def load_fernet_key() -> bytes:
    global _fernet_key_cache
    if _fernet_key_cache is None:
        settings = get_settings()
        if settings.credential_encryption_key:
            _fernet_key_cache = settings.credential_encryption_key.encode()
        else:
            _fernet_key_cache = Path(settings.credential_encryption_key_path).read_bytes()
    return _fernet_key_cache


def generate_key() -> str:
    """Mint a key for an operator to paste into their platform's secrets.
    Exposed for `python -m app.keygen` (see DEPLOYMENT.md)."""
    return Fernet.generate_key().decode()


def encrypt_api_key(plaintext: str) -> str:
    return Fernet(load_fernet_key()).encrypt(plaintext.encode()).decode()


def decrypt_api_key(ciphertext: str) -> str:
    return Fernet(load_fernet_key()).decrypt(ciphertext.encode()).decode()


def display_prefix(plaintext: str) -> str:
    return plaintext[:8] + "..." if len(plaintext) > 8 else plaintext
