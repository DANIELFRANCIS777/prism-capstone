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


def get_provider(name: str) -> ProviderAdapter:
    return provider_registry()[name]
