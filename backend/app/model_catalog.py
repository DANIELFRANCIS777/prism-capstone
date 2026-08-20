"""Backs GET /v1/models - the OpenAI-compatible model-discovery endpoint.

Served entirely from our own registered catalog (config/gateway_config.json),
never by calling the upstream provider's own /models on each request: that
would put a third-party network call on a metadata path, and the answer
barely changes. Keeping the catalog fresh as providers deprecate models is a
background-sync concern (see ROADMAP.md), not a per-request one.

Pure functions, no DB and no HTTP - same shape as app/auth.py's
enforce_allowlist, so it's unit-testable without either.
"""

import time

from app.config import known_catalog_names, load_gateway_config, model_provider_map

# gateway_config.json's providers[].models[] is a flat list of name strings
# with no per-model metadata, so there's no real creation date to report.
# OpenAI clients don't validate this field; a stable per-process value keeps
# the response well-formed without inventing a fact we don't have.
_CATALOG_CREATED_AT = int(time.time())

# An alias isn't owned by any one provider - `fast` fails over from alpha to
# beta, and `auto` has no single primary at all - so reporting whichever
# provider is primary today would be wrong the moment fallbacks change.
_ALIAS_OWNER = "prism"


def list_models_for_allowlist(model_allowlist: list[str]) -> list[dict]:
    """The catalog, filtered to what this caller's key may actually request.

    Names in the allowlist that no longer exist in config are dropped rather
    than raising: an operator retiring a model shouldn't break discovery for
    every key that still lists it, and advertising a model that would 404 on
    use is worse than omitting it."""
    aliases = set(load_gateway_config()["model_aliases"])
    provider_map = model_provider_map()

    return [
        {
            "id": name,
            "object": "model",
            "created": _CATALOG_CREATED_AT,
            "owned_by": _ALIAS_OWNER if name in aliases else provider_map[name],
        }
        for name in sorted(set(model_allowlist) & known_catalog_names())
    ]
