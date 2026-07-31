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
    (splicing two providers' output into one stream is not acceptable)."""
    start = time.monotonic()
    usage: dict = {}
    done_seen = False
    mid_stream_error: UpstreamError | None = None

    line = handle.first_line
    try:
        while line is not None:
            yield f"{line}\n\n".encode()
            if line.strip() == "data: [DONE]":
                done_seen = True
                break
            payload = _parse_sse_json(line)
            if isinstance(payload, dict) and "usage" in payload:
                usage.update(payload["usage"])
            line = await handle.generator.__anext__()
    except StopAsyncIteration:
        pass
    except UpstreamError as exc:
        mid_stream_error = exc

    latency_ms = int((time.monotonic() - start) * 1000)

    if not done_seen:
        message = (
            str(mid_stream_error)
            if mid_stream_error
            else f"{handle.route.provider_name} closed the connection before completion"
        )
        yield _error_event(message)
        yield b"data: [DONE]\n\n"
        await log_request(
            db,
            virtual_key=key.virtual_key,
            requested_model=requested_model,
            resolved_provider=handle.route.provider_name,
            resolved_model=handle.route.model,
            status="upstream_error",
            cache="miss",
            fallback=handle.fallback,
            retries=handle.retries,
            latency_ms=latency_ms,
            route_reason=route_reason,
        )
        return

    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    cost_usd = compute_cost_usd(handle.route.model, prompt_tokens, completion_tokens)

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
