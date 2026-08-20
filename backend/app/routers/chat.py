import logging
import time

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import UpstreamError
from app.auth import GatewayError, authenticate, enforce_allowlist
from app.budget import current_year_month, enforce_budget, record_usage
from app.cache import find_cache_hit, record_cache_hit, store_cache_entry
from app.config import get_settings
from app.db import get_db
from app.model_catalog import list_models_for_allowlist
from app.provider_credentials import load_org_provider_credentials
from app.rate_limit import enforce_rate_limit
from app.request_log import log_request
from app.routing.aliases import RouteNotImplementedError, UnknownModelError
from app.routing.auto_router import resolve_route
from app.routing.dispatch import dispatch_non_streaming, dispatch_streaming
from app.routing.pricing import compute_cost_usd
from app.streaming import forward_stream

logger = logging.getLogger(__name__)

router = APIRouter()


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


async def _log_rejection(
    db: AsyncSession,
    status: str,
    virtual_key_id: int | None = None,
    requested_model: str = "",
    route_reason: str | None = None,
    latency_ms: int = 0,
) -> None:
    """Every rejection path calls this before raising - AGENTS.md: 'Log every
    request, including rejections.'

    Writes both the audit row (request_logs, the billing/reporting source of
    truth) and an application log line. The row is queryable after the fact;
    the log line is what shows up while an incident is happening."""
    entry = await log_request(
        db,
        virtual_key_id=virtual_key_id,
        requested_model=requested_model,
        status=status,
        route_reason=route_reason,
        latency_ms=latency_ms,
    )
    logger.info(
        "request rejected",
        extra={
            "request_id": entry.request_id,
            "status": status,
            "virtual_key_id": virtual_key_id,
            "requested_model": requested_model,
        },
    )


@router.get("/v1/models")
async def list_models(request: Request, db: AsyncSession = Depends(get_db)):
    """OpenAI-compatible model discovery, scoped to the calling key.

    Same bearer-key auth as the data plane, but deliberately none of the rest
    of the pipeline: no rate limit, budget, cache, or request_logs row. This
    is a metadata lookup, not a billable request, and there's no requested
    model to log.

    Scoping the listing to the key's own allowlist is also what makes a
    separate provider-validation step unnecessary - a key that may only reach
    one provider simply never sees another provider's models here."""
    key = await authenticate(request.headers.get("authorization"), db)
    return {"object": "list", "data": list_models_for_allowlist(key.model_allowlist)}


@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request, response: Response, db: AsyncSession = Depends(get_db)
):
    # Wall time for this request, recorded on every logged outcome below.
    # Streaming measures its own span instead (app/streaming.py), since the
    # meaningful duration there runs past this handler's return.
    started = time.monotonic()
    authorization = request.headers.get("authorization")

    try:
        key = await authenticate(authorization, db)
    except GatewayError as exc:
        await _log_rejection(db, "rejected_auth", latency_ms=_elapsed_ms(started))
        raise exc

    try:
        body = await request.json()
    except ValueError as exc:
        await _log_rejection(db, "rejected_invalid_request", key.id, latency_ms=_elapsed_ms(started))
        raise GatewayError(400, "Request body is not valid JSON", "invalid_request_error") from exc

    requested_model = body.get("model")
    messages = body.get("messages")
    stream = bool(body.get("stream"))

    if not requested_model:
        await _log_rejection(db, "rejected_invalid_request", key.id, latency_ms=_elapsed_ms(started))
        raise GatewayError(400, "'model' is required", "invalid_request_error")
    if not isinstance(messages, list) or not messages:
        await _log_rejection(db, "rejected_invalid_request", key.id, requested_model, latency_ms=_elapsed_ms(started))
        raise GatewayError(400, "'messages' must be a non-empty list", "invalid_request_error")

    try:
        chain, route_reason = resolve_route(requested_model, messages)
    except UnknownModelError as exc:
        await _log_rejection(db, "rejected_not_found", key.id, requested_model, latency_ms=_elapsed_ms(started))
        raise GatewayError(404, str(exc), "not_found_error") from exc
    except RouteNotImplementedError as exc:
        await _log_rejection(db, "rejected_not_implemented", key.id, requested_model, latency_ms=_elapsed_ms(started))
        raise GatewayError(501, str(exc), "not_implemented_error") from exc

    try:
        enforce_allowlist(key, requested_model)
    except GatewayError as exc:
        await _log_rejection(db, "rejected_allowlist", key.id, requested_model, route_reason, latency_ms=_elapsed_ms(started))
        raise exc

    try:
        await enforce_rate_limit(db, key)
    except GatewayError as exc:
        await _log_rejection(db, "rejected_rate_limit", key.id, requested_model, route_reason, latency_ms=_elapsed_ms(started))
        raise exc

    year_month = current_year_month()
    try:
        await enforce_budget(db, key, year_month)
    except GatewayError as exc:
        await _log_rejection(db, "rejected_budget", key.id, requested_model, route_reason, latency_ms=_elapsed_ms(started))
        raise exc

    settings = get_settings()

    provider_credentials = None
    if settings.byok_dispatch_enabled and key.org_id is not None:
        provider_credentials = await load_org_provider_credentials(db, key.org_id)

    # Cache lookup: non-streaming only (documented simplification - see
    # docs/IMPLEMENTATION_GUIDE.md FAQ on streaming + cache). Scoped per key,
    # keyed by the literal requested alias/model, never shared across tenants.
    if not stream and key.cache_enabled:
        cached = await find_cache_hit(
            db, key.id, requested_model, messages, key.cache_similarity_threshold
        )
        if cached is not None:
            await record_cache_hit(db, cached)
            await log_request(
                db,
                virtual_key_id=key.id,
                requested_model=requested_model,
                resolved_provider=cached.resolved_provider,
                resolved_model=cached.resolved_model,
                status="ok",
                cache="hit",
                fallback=False,
                cost_usd=0,
                route_reason=route_reason,
                latency_ms=_elapsed_ms(started),
            )
            response.headers["x-prism-provider"] = f"{cached.resolved_provider}/{cached.resolved_model}"
            response.headers["x-prism-cache"] = "hit"
            response.headers["x-prism-fallback"] = "false"
            response.headers["x-prism-cost-usd"] = "0"
            return cached.response_body

    if stream:
        try:
            handle = await dispatch_streaming(
                chain, messages, settings.upstream_timeout_seconds, provider_credentials
            )
        except UpstreamError as exc:
            await log_request(
                db, virtual_key_id=key.id, requested_model=requested_model,
                status="upstream_error", retries=getattr(exc, "retries", 0),
                route_reason=route_reason, latency_ms=_elapsed_ms(started),
            )
            raise GatewayError(502, str(exc), "upstream_error") from exc

        response_headers = {
            "x-prism-provider": f"{handle.route.provider_name}/{handle.route.model}",
            "x-prism-cache": "miss",
            "x-prism-fallback": "true" if handle.fallback else "false",
        }
        return StreamingResponse(
            forward_stream(db, key, requested_model, handle, route_reason),
            media_type="text/event-stream",
            headers=response_headers,
        )

    try:
        result = await dispatch_non_streaming(
            chain, messages, settings.upstream_timeout_seconds, provider_credentials
        )
    except UpstreamError as exc:
        await log_request(
            db, virtual_key_id=key.id, requested_model=requested_model,
            status="upstream_error", retries=getattr(exc, "retries", 0),
            route_reason=route_reason, latency_ms=_elapsed_ms(started),
        )
        raise GatewayError(502, str(exc), "upstream_error") from exc

    usage = result.body.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    cost_usd = compute_cost_usd(result.route.model, prompt_tokens, completion_tokens)

    if key.cache_enabled:
        await store_cache_entry(
            db, key.id, requested_model, messages, result.body,
            result.route.provider_name, result.route.model,
        )

    await record_usage(db, key, year_month, cost_usd, prompt_tokens, completion_tokens)
    await log_request(
        db,
        virtual_key_id=key.id,
        requested_model=requested_model,
        resolved_provider=result.route.provider_name,
        resolved_model=result.route.model,
        status="ok",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
        cache="miss",
        fallback=result.fallback,
        retries=result.retries,
        route_reason=route_reason,
        latency_ms=_elapsed_ms(started),
    )

    response.headers["x-prism-provider"] = f"{result.route.provider_name}/{result.route.model}"
    response.headers["x-prism-cache"] = "miss"
    response.headers["x-prism-fallback"] = "true" if result.fallback else "false"
    response.headers["x-prism-cost-usd"] = str(cost_usd)
    return result.body
