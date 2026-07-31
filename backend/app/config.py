import json
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
DATA_DIR = REPO_ROOT / "data"
CONFIG_DIR = BACKEND_ROOT / "config"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_ROOT / ".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://prism:prism@localhost:5432/prism"
    upstream_timeout_seconds: float = 10.0
    # Selects which file in config/ to load. Local dev uses the default
    # (providers on localhost); the Docker Compose gateway service overrides
    # this to gateway_config.docker.json (providers reached by service name).
    gateway_config_file: str = "gateway_config.json"

    # Admin auth: JWT (RS256) access + refresh tokens, see app/admin_auth.py.
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
