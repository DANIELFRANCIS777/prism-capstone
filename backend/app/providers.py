from functools import lru_cache

from app.adapters.base import ProviderAdapter
from app.adapters.openai_compatible import OpenAICompatibleAdapter
from app.config import load_gateway_config


@lru_cache
def provider_registry() -> dict[str, ProviderAdapter]:
    config = load_gateway_config()
    return {
        p["name"]: OpenAICompatibleAdapter(
            name=p["name"], base_url=p["base_url"], api_key=p["api_key"]
        )
        for p in config["providers"]
    }


def get_provider(name: str, api_key_override: str | None = None) -> ProviderAdapter:
    """api_key_override lets a tenant's own BYOK credential (app/
    provider_credentials.py) stand in for the platform's shared static key
    for this one call, without touching the cached registry every other
    caller uses. Building a fresh adapter here costs nothing extra:
    OpenAICompatibleAdapter already opens a new httpx.AsyncClient per call
    (see adapters/openai_compatible.py), so there's no pooled connection to
    lose by not reusing the cached instance."""
    base = provider_registry()[name]
    if api_key_override is None:
        return base
    return OpenAICompatibleAdapter(name=base.name, base_url=base.base_url, api_key=api_key_override)
