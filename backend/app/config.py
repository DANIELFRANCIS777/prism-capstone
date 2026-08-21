import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
DATA_DIR = REPO_ROOT / "data"
CONFIG_DIR = BACKEND_ROOT / "config"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_ROOT / ".env", extra="ignore")

    # "production" turns on the deployment safety checks in
    # validate_production_settings() - notably refusing to boot with the
    # well-known default admin password. Left as "development" so local dev
    # and the demo compose stack keep working with zero configuration.
    environment: Literal["development", "production"] = "development"

    database_url: str = "postgresql+asyncpg://prism:prism@localhost:5432/prism"
    upstream_timeout_seconds: float = 10.0
    # Applies pending Alembic revisions during startup, serialized across
    # replicas by an advisory lock (app/migrate.py). Turn off if you'd rather
    # run `alembic upgrade head` as an explicit release step.
    run_migrations_on_startup: bool = True
    # Selects which file in config/ to load. Local dev uses the default
    # (providers on localhost); the Docker Compose gateway service overrides
    # this to gateway_config.docker.json (providers reached by service name).
    gateway_config_file: str = "gateway_config.json"

    # Admin auth: JWT (RS256) access + refresh tokens, see app/admin_auth.py.
    # Supply the PEM text directly via these env vars on any host with an
    # ephemeral filesystem; otherwise the *_path files are used, and
    # generated on first startup if absent (see app/jwt_keys.py).
    jwt_private_key: str = ""
    jwt_public_key: str = ""
    jwt_private_key_path: str = str(BACKEND_ROOT / "keys" / "jwt_private.pem")
    jwt_public_key_path: str = str(BACKEND_ROOT / "keys" / "jwt_public.pem")
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    # Bootstrap admin account, seeded on startup only if admin_users is empty -
    # there's no signup flow, so this is how the first account gets created.
    # Change this in any non-throwaway environment.
    admin_bootstrap_username: str = "admin"
    admin_bootstrap_password: str = "prism-admin-dev-password"

    # Comma-separated origins allowed to call the API from a browser (the
    # ops console). Simpler to hand-edit in .env than a JSON list.
    cors_allow_origins: str = "http://localhost:5173,http://localhost:4173"

    # BYOK provider credentials (app/credential_crypto.py, app/provider_credentials.py).
    # Same resolution as the JWT keys above - env var wins, file is the
    # local-dev fallback. Losing this key makes every stored credential
    # permanently undecryptable, so production must supply it explicitly.
    credential_encryption_key: str = ""
    credential_encryption_key_path: str = str(BACKEND_ROOT / "keys" / "credential_encryption.key")

    # Self-serve tenant accounts (app/routers/user.py). The ceilings are the
    # operator's actual cost-exposure control on a publicly reachable
    # instance - a self-serve org can set anything at or below these, never
    # above, and self-serve is capped at one key per org (no aggregate
    # multi-key tracking yet - see ROADMAP.md).
    self_serve_signup_enabled: bool = True
    self_serve_max_requests_per_minute: int = 60
    self_serve_max_monthly_budget_usd: float = 50.0
    self_serve_max_keys_per_org: int = 1
    # Brute-force protection for the unauthenticated auth endpoints
    # (app/auth_throttle.py). The per-minute ceiling caps request volume per
    # IP/email; the lockout stops slow guessing that stays under it.
    auth_max_attempts_per_minute: int = 10
    auth_lockout_threshold: int = 5
    auth_lockout_minutes: int = 15

    # Periodic pruning of tables that otherwise grow forever - stale
    # rate-limit windows and expired refresh tokens (app/background.py).
    # Runs in-process, serialized across replicas by an advisory lock.
    maintenance_enabled: bool = True
    maintenance_interval_seconds: int = 3600
    rate_limit_window_retention_seconds: int = 600

    log_level: str = "INFO"

    # Whether dispatch consults an org's stored BYOK credential at all.
    # Instantly flippable without a redeploy if something looks wrong with it -
    # every seeded/operator-provisioned key is unaffected either way (org_id
    # is None for those, so they never reach this path).
    byok_dispatch_enabled: bool = True

    @property
    def cors_allow_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def load_model_pricing() -> dict:
    with open(DATA_DIR / "model_pricing.json") as f:
        data = json.load(f)
    data.pop("_comment", None)
    return data


@lru_cache
def load_gateway_config() -> dict:
    with open(CONFIG_DIR / get_settings().gateway_config_file) as f:
        return json.load(f)


@lru_cache
def load_seed_keys() -> list[dict]:
    with open(DATA_DIR / "seed_keys.json") as f:
        data = json.load(f)
    return data["tenants"]


@lru_cache
def model_provider_map() -> dict[str, str]:
    config = load_gateway_config()
    return {model: p["name"] for p in config["providers"] for model in p["models"]}


def known_catalog_names() -> set[str]:
    """Every alias name plus every literal model name any provider registers -
    the full universe of strings valid in a chat-completions "model" field.
    Shared by self-serve allowlist validation and GET /v1/models so the two
    can't drift."""
    return set(load_gateway_config()["model_aliases"]) | set(model_provider_map())


DEFAULT_ADMIN_PASSWORD = "prism-admin-dev-password"


def validate_production_settings(settings: "Settings | None" = None) -> None:
    """Refuse to start a production deployment with known-insecure defaults.

    The bootstrap admin password is the dangerous one: seed_admin_user()
    writes it into the database on first startup and then never re-seeds
    (it no-ops once admin_users is non-empty), so an operator who forgets to
    override it ends up with a permanent, publicly-documented password on an
    account that can read every tenant's usage and logs. Failing loudly at
    boot is the only reliable point to catch that.

    Takes settings as an argument so it's unit-testable without touching the
    lru_cache'd global."""
    if settings is None:
        settings = get_settings()
    if settings.environment != "production":
        return

    problems = []
    if settings.admin_bootstrap_password == DEFAULT_ADMIN_PASSWORD:
        problems.append(
            "ADMIN_BOOTSTRAP_PASSWORD is still the default value from .env.example. "
            "Set a real password - this account can read every tenant's usage and logs."
        )
    # Key material generated into the filesystem does not survive a redeploy
    # on a host with an ephemeral disk, which is most managed platforms'
    # default. For the JWT pair that silently logs everyone out on each
    # release; for the credential key it permanently destroys every stored
    # BYOK credential. Neither failure is visible until someone tries to use
    # the system, so require the secrets to be explicit in production.
    if not settings.credential_encryption_key:
        problems.append(
            "CREDENTIAL_ENCRYPTION_KEY is not set. Without it the key is generated onto "
            "local disk, and if that disk is ephemeral every stored BYOK credential "
            "becomes permanently undecryptable on the next deploy. "
            "Generate one with: python -m app.keygen"
        )
    if not (settings.jwt_private_key and settings.jwt_public_key):
        problems.append(
            "JWT_PRIVATE_KEY / JWT_PUBLIC_KEY are not both set. Without them the pair is "
            "generated onto local disk, and if that disk is ephemeral every issued token "
            "is invalidated on each deploy. Generate them with: python -m app.keygen"
        )
    if problems:
        raise RuntimeError(
            "Refusing to start with ENVIRONMENT=production and insecure defaults:\n  - "
            + "\n  - ".join(problems)
            + "\n\n"
            + _secret_visibility_report(settings)
        )


def _secret_visibility_report(settings: "Settings") -> str:
    """What the process can actually see, so a failed deploy distinguishes
    'never set it' from 'set it somewhere this container isn't reading'.

    Only presence and length are reported, never any value - this goes into
    logs that may be shipped off-host. Length is enough to catch the common
    paste accidents (empty string, a stray quote) without disclosing key
    material.
    """
    watched = {
        "ENVIRONMENT": settings.environment,
        "DATABASE_URL": settings.database_url,
        "ADMIN_BOOTSTRAP_PASSWORD": settings.admin_bootstrap_password,
        "CREDENTIAL_ENCRYPTION_KEY": settings.credential_encryption_key,
        "JWT_PRIVATE_KEY": settings.jwt_private_key,
        "JWT_PUBLIC_KEY": settings.jwt_public_key,
        "CORS_ALLOW_ORIGINS": settings.cors_allow_origins,
    }
    lines = ["What this process can see (values redacted):"]
    for name, value in watched.items():
        if name in ("ENVIRONMENT", "CORS_ALLOW_ORIGINS"):
            # Not secret, and being able to read them back is the fastest way
            # to spot a typo or a variable set on the wrong service.
            lines.append(f"  {name} = {value!r}")
        elif value:
            lines.append(f"  {name} is set ({len(value)} chars)")
        else:
            lines.append(f"  {name} is EMPTY or unset")
    lines.append(
        "\nIf a variable you set shows as unset here, it isn't reaching this "
        "container: check it's on the gateway service (not the console), saved, "
        "and that the deploy ran after saving."
    )
    return "\n".join(lines)


def validate_pricing_coverage(
    reachable_models: set[str] | None = None, priced_models: set[str] | None = None
) -> None:
    """Every model reachable via the gateway config must have a price entry -
    otherwise real provider usage against it would be silently metered as
    free (compute_cost_usd returns 0.0 for an unpriced model) and bypass
    budget enforcement with no signal that pricing data is missing. Checked
    once at startup so a config/pricing mismatch fails loudly before serving
    any traffic, rather than leaking cost silently on the hot path.

    Accepts explicit sets (rather than always reading the real, lru_cache'd
    config) so it's trivially unit-testable without faking out the cache."""
    if reachable_models is None:
        reachable_models = set(model_provider_map())
    if priced_models is None:
        priced_models = set(load_model_pricing())
    missing = sorted(reachable_models - priced_models)
    if missing:
        raise RuntimeError(
            "Model(s) reachable via the gateway config have no entry in "
            f"data/model_pricing.json: {missing}. Add pricing before serving traffic, "
            "or usage against them will be silently metered as free."
        )
