"""Key material must survive a deploy.

On a host with an ephemeral filesystem - most managed platforms' default -
a key generated into keys/ is gone on the next release. For the JWT pair
that logs everyone out; for the credential encryption key it makes every
stored BYOK credential permanently undecryptable. These tests pin the env
path that makes a real deployment safe, and the startup guard that stops an
operator from deploying into the broken configuration by accident.
"""

import pytest

from app import credential_crypto, jwt_keys, jwt_tokens
from app.config import Settings, get_settings, validate_production_settings


@pytest.fixture
def env_keys(monkeypatch):
    """Supply key material by env only, with the file paths pointed at
    somewhere that does not exist - so anything falling back to disk fails
    loudly instead of silently passing."""
    private_pem, public_pem = jwt_keys.generate_key_pair()
    fernet_key = credential_crypto.generate_key()

    monkeypatch.setenv("JWT_PRIVATE_KEY", private_pem)
    monkeypatch.setenv("JWT_PUBLIC_KEY", public_pem)
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", fernet_key)
    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", "/nonexistent/private.pem")
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", "/nonexistent/public.pem")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY_PATH", "/nonexistent/cred.key")

    get_settings.cache_clear()
    monkeypatch.setattr(jwt_keys, "_private_key_cache", None)
    monkeypatch.setattr(jwt_keys, "_public_key_cache", None)
    monkeypatch.setattr(credential_crypto, "_fernet_key_cache", None)
    yield
    get_settings.cache_clear()


def test_jwt_round_trips_using_only_env_key_material(env_keys):
    token = jwt_tokens.encode({"sub": "deploy-test", "type": "access", "scope": "user"})
    assert jwt_tokens.decode(token)["sub"] == "deploy-test"


def test_byok_credential_round_trips_using_only_env_key_material(env_keys):
    ciphertext = credential_crypto.encrypt_api_key("gsk_provider_secret")
    assert credential_crypto.decrypt_api_key(ciphertext) == "gsk_provider_secret"


def test_ensure_helpers_do_not_touch_disk_when_env_keys_are_set(env_keys):
    """Both would raise trying to write to /nonexistent if they ignored env."""
    jwt_keys.ensure_keys_exist()
    credential_crypto.ensure_fernet_key_exists()


def test_pem_with_escaped_newlines_is_accepted(env_keys, monkeypatch):
    """Some platforms only round-trip single-line secret values, turning the
    PEM's newlines into a literal backslash-n. That must not surface as an
    opaque 'could not deserialize key data' at startup."""
    private_pem, public_pem = jwt_keys.generate_key_pair()
    monkeypatch.setenv("JWT_PRIVATE_KEY", private_pem.replace("\n", "\\n"))
    monkeypatch.setenv("JWT_PUBLIC_KEY", public_pem.replace("\n", "\\n"))
    get_settings.cache_clear()
    monkeypatch.setattr(jwt_keys, "_private_key_cache", None)
    monkeypatch.setattr(jwt_keys, "_public_key_cache", None)

    token = jwt_tokens.encode({"sub": "escaped", "type": "access"})
    assert jwt_tokens.decode(token)["sub"] == "escaped"


def test_production_refuses_to_start_without_the_credential_key():
    """The failure this prevents is silent and unrecoverable: deploy, take
    real BYOK credentials, redeploy, and every one of them is now garbage."""
    settings = Settings(
        environment="production",
        admin_bootstrap_password="a-real-password",
        jwt_private_key="x",
        jwt_public_key="y",
        credential_encryption_key="",
    )
    with pytest.raises(RuntimeError, match="CREDENTIAL_ENCRYPTION_KEY"):
        validate_production_settings(settings)


def test_production_refuses_to_start_without_the_jwt_pair():
    settings = Settings(
        environment="production",
        admin_bootstrap_password="a-real-password",
        credential_encryption_key="k",
        jwt_private_key="",
        jwt_public_key="",
    )
    with pytest.raises(RuntimeError, match="JWT_PRIVATE_KEY"):
        validate_production_settings(settings)


def test_production_starts_when_every_secret_is_supplied():
    validate_production_settings(
        Settings(
            environment="production",
            admin_bootstrap_password="a-real-password",
            credential_encryption_key="k",
            jwt_private_key="x",
            jwt_public_key="y",
        )
    )


def test_development_still_starts_with_no_secrets_configured():
    """Local dev and the Compose stack must keep working with zero setup."""
    validate_production_settings(Settings(environment="development"))


def test_the_failure_report_never_leaks_secret_values():
    """The report goes into logs that may be shipped off-host, so it must
    show presence and length only - never the material itself."""
    secret_password = "super-secret-password-value"
    secret_jwt = "-----BEGIN PRIVATE KEY-----\nSECRETKEYMATERIAL\n-----END PRIVATE KEY-----\n"
    settings = Settings(
        environment="production",
        admin_bootstrap_password=secret_password,
        jwt_private_key=secret_jwt,
        jwt_public_key="public-part",
        credential_encryption_key="",
    )
    with pytest.raises(RuntimeError) as exc_info:
        validate_production_settings(settings)

    message = str(exc_info.value)
    assert secret_password not in message
    assert "SECRETKEYMATERIAL" not in message
    assert secret_jwt not in message
    # But it must still be actionable about which one is missing.
    assert "CREDENTIAL_ENCRYPTION_KEY is EMPTY or unset" in message
    assert "JWT_PRIVATE_KEY is set" in message


def test_the_failure_report_shows_non_secret_values_verbatim():
    """CORS and ENVIRONMENT are not secret, and reading them back is the
    fastest way to spot a typo or a variable set on the wrong service."""
    settings = Settings(
        environment="production",
        admin_bootstrap_password="real",
        cors_allow_origins="https://typo-here.onrender.com",
        credential_encryption_key="",
    )
    with pytest.raises(RuntimeError) as exc_info:
        validate_production_settings(settings)
    assert "https://typo-here.onrender.com" in str(exc_info.value)
