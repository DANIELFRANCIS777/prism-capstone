import json
import time
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import UpstreamError
from app.budget import current_year_month, record_usage
from app.models import VirtualKey
from app.request_log import log_request
from app.routing.dispatch import StreamHandle
from app.routing.pricing import compute_cost_usd


def _parse_sse_json(line: str):
    if not line.startswith("data: "):
        return None
    payload = line[len("data: "):]
    if payload.strip() == "[DONE]":
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def _error_event(message: str) -> bytes:
    body = {"error": {"message": message, "type": "upstream_error", "code": "upstream_error"}}
    return f"data: {json.dumps(body)}\n\n".encode()


async def forward_stream(
    db: AsyncSession,
    key: VirtualKey,
    requested_model: str,
    handle: StreamHandle,
    route_reason: str | None = None,
) -> AsyncIterator[bytes]:
    """Forwards upstream SSE lines to the client as they arrive - no buffering.
    A failure here is a MID-STREAM failure: bytes may already be with the client,
    so we terminate with an SSE error event rather than retrying or failing over
    (splicing two providers' output into one stream is not acceptable).

    A client disconnect is different again: Starlette closes this generator by
    raising GeneratorExit at the suspended yield, so we can no longer send
    anything - but the upstream may already have generated (and billed) real
    tokens, so we still need to record usage/log the request. Usage is parsed
    out of each line *before* it's yielded specifically so that a disconnect
    during the yield of the final, usage-bearing chunk doesn't lose it."""
    start = time.monotonic()
    usage: dict = {}
    done_seen = False
    mid_stream_error: UpstreamError | None = None
    client_disconnected = False

    line = handle.first_line
    try:
        while line is not None:
            payload = _parse_sse_json(line)
            if isinstance(payload, dict) and "usage" in payload:
                usage.update(payload["usage"])
            is_done = line.strip() == "data: [DONE]"
            yield f"{line}\n\n".encode()
            if is_done:
                done_seen = True
                break
            line = await handle.generator.__anext__()
    except StopAsyncIteration:
        pass
    except UpstreamError as exc:
        mid_stream_error = exc
    except GeneratorExit:
        # Can't yield anymore from here - just record what we know and let the
        # generator close normally (do not re-raise: catching and returning is
        # the correct way to run cleanup in response to GeneratorExit).
        client_disconnected = True

    latency_ms = int((time.monotonic() - start) * 1000)
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    cost_usd = compute_cost_usd(handle.route.model, prompt_tokens, completion_tokens) if usage else 0.0

    if not done_seen:
        if not client_disconnected:
            message = (
                str(mid_stream_error)
                if mid_stream_error
                else f"{handle.route.provider_name} closed the connection before completion"
            )
            yield _error_event(message)
            yield b"data: [DONE]\n\n"

        if cost_usd:
            await record_usage(db, key, current_year_month(), cost_usd, prompt_tokens, completion_tokens)
        await log_request(
            db,
            virtual_key=key.virtual_key,
            requested_model=requested_model,
            resolved_provider=handle.route.provider_name,
            resolved_model=handle.route.model,
            status="client_disconnected" if client_disconnected else "upstream_error",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            cache="miss",
            fallback=handle.fallback,
            retries=handle.retries,
            latency_ms=latency_ms,
            route_reason=route_reason,
        )
        return

    await record_usage(db, key, current_year_month(), cost_usd, prompt_tokens, completion_tokens)
    await log_request(
        db,
        virtual_key=key.virtual_key,
        requested_model=requested_model,
        resolved_provider=handle.route.provider_name,
        resolved_model=handle.route.model,
        status="ok",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
        cache="miss",
        fallback=handle.fallback,
        retries=handle.retries,
        latency_ms=latency_ms,
        route_reason=route_reason,
    )
