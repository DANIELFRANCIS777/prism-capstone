import asyncio
from dataclasses import dataclass
from typing import Any

from app.adapters.base import UpstreamError
from app.config import load_gateway_config
from app.providers import get_provider
from app.routing.aliases import ResolvedRoute

RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def _is_retryable(exc: UpstreamError) -> bool:
    """No status code means a timeout or connection failure - always retryable.
    A definite 4xx (other than 408/429) is a real rejection; retrying the same
    candidate won't help, so move straight to the next one in the chain."""
    return exc.status_code is None or exc.status_code in RETRYABLE_STATUS_CODES


def retry_policy() -> dict:
    return load_gateway_config()["retry"]


@dataclass
class DispatchResult:
    body: dict[str, Any]
    route: ResolvedRoute
    fallback: bool
    retries: int


async def dispatch_non_streaming(
    chain: list[ResolvedRoute], messages: list[dict], timeout: float
) -> DispatchResult:
    policy = retry_policy()
    total_retries = 0
    last_error: UpstreamError | None = None

    for i, route in enumerate(chain):
        adapter = get_provider(route.provider_name)
        backoff = policy["initial_backoff_ms"] / 1000
        for attempt in range(policy["max_attempts"]):
            try:
                body = await adapter.chat_completion(route.model, messages, timeout)
                return DispatchResult(
                    body=body, route=route, fallback=(i > 0), retries=total_retries
                )
            except UpstreamError as exc:
                last_error = exc
                is_last_attempt = attempt == policy["max_attempts"] - 1
                if not _is_retryable(exc) or is_last_attempt:
                    break
                total_retries += 1
                await asyncio.sleep(backoff)
                backoff *= policy["backoff_multiplier"]

    last_error.retries = total_retries
    raise last_error


@dataclass
class StreamHandle:
    generator: Any  # the adapter's already-opened async generator, first line consumed
    first_line: str | None
    route: ResolvedRoute
    fallback: bool
    retries: int


async def dispatch_streaming(
    chain: list[ResolvedRoute], messages: list[dict], timeout: float
) -> StreamHandle:
    """Opens the upstream stream. Retries/fails over only while opening - once a
    candidate yields its first line, that candidate owns the request; any later
    failure is the caller's responsibility to handle as a mid-stream failure."""
    policy = retry_policy()
    total_retries = 0
    last_error: UpstreamError | None = None

    for i, route in enumerate(chain):
        adapter = get_provider(route.provider_name)
        backoff = policy["initial_backoff_ms"] / 1000
        for attempt in range(policy["max_attempts"]):
            generator = adapter.stream_chat_completion(route.model, messages, timeout)
            try:
                first_line = await generator.__anext__()
            except StopAsyncIteration:
                first_line = None
            except UpstreamError as exc:
                last_error = exc
                is_last_attempt = attempt == policy["max_attempts"] - 1
                if not _is_retryable(exc) or is_last_attempt:
                    break
                total_retries += 1
                await asyncio.sleep(backoff)
                backoff *= policy["backoff_multiplier"]
                continue
            return StreamHandle(
                generator=generator,
                first_line=first_line,
                route=route,
                fallback=(i > 0),
                retries=total_retries,
            )

    last_error.retries = total_retries
    raise last_error
