from dataclasses import dataclass

from app.config import load_gateway_config, model_provider_map


class UnknownModelError(Exception):
    """Alias or model name is not registered in the gateway config."""


class RouteNotImplementedError(Exception):
    """Alias resolves to a route this milestone doesn't implement yet (e.g. `auto`)."""


@dataclass
class ResolvedRoute:
    provider_name: str
    model: str


def resolve_candidate_chain(alias_or_model: str) -> list[ResolvedRoute]:
    """Resolve an alias to its primary model plus ordered fallbacks (or a bare
    model name to a single-candidate chain). `auto` classification lands in
    Milestone 4."""
    config = load_gateway_config()
    aliases = config["model_aliases"]
    provider_map = model_provider_map()

    if alias_or_model in aliases:
        alias_cfg = aliases[alias_or_model]
        if "primary" not in alias_cfg:
            raise RouteNotImplementedError(
                f"alias '{alias_or_model}' has no static primary route yet"
            )
        models = [alias_cfg["primary"], *alias_cfg.get("fallbacks", [])]
    else:
        models = [alias_or_model]

    routes = []
    for model in models:
        provider_name = provider_map.get(model)
        if provider_name is None:
            raise UnknownModelError(f"no provider registered for model '{model}'")
        routes.append(ResolvedRoute(provider_name=provider_name, model=model))
    return routes
