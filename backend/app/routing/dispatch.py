import asyncio
import time
from dataclasses import dataclass
from typing import Any

from app.adapters.base import UpstreamError
from app.config import load_gateway_config
from app.providers import get_provider
from app.routing.aliases import ResolvedRoute

RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}

# Caps the *total* time one request can spend across every retry and fallback
# candidate combined. Without this, a hung (not fast-failing) primary could
# cost max_attempts * timeout before failover even starts, and each fallback
# candidate could repeat that - an unbounded worst case despite every
# individual upstream call having its own timeout.
MAX_DISPATCH_TIMEOUT_MULTIPLIER = 3


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
    deadline = time.monotonic() + timeout * MAX_DISPATCH_TIMEOUT_MULTIPLIER

    for i, route in enumerate(chain):
        if time.monotonic() >= deadline:
            break
        adapter = get_provider(route.provider_name)
        backoff = policy["initial_backoff_ms"] / 1000
        for attempt in range(policy["max_attempts"]):
            if time.monotonic() >= deadline:
                break
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

    if last_error is None:
        last_error = UpstreamError("no provider candidate could be reached before the dispatch deadline")
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
    failure is the caller's responsibility to handle as a mid-stream failure.

    A candidate that closes the connection with zero lines (StopAsyncIteration
    on the very first read) is treated as a failure, not a successful open -
    it gets the same retry-then-failover treatment as an UpstreamError."""
    policy = retry_policy()
    total_retries = 0
    last_error: UpstreamError | None = None
    deadline = time.monotonic() + timeout * MAX_DISPATCH_TIMEOUT_MULTIPLIER

    for i, route in enumerate(chain):
        if time.monotonic() >= deadline:
            break
        adapter = get_provider(route.provider_name)
        backoff = policy["initial_backoff_ms"] / 1000
        for attempt in range(policy["max_attempts"]):
            if time.monotonic() >= deadline:
                break
            generator = adapter.stream_chat_completion(route.model, messages, timeout)
            try:
                first_line = await generator.__anext__()
            except StopAsyncIteration:
                exc = UpstreamError(f"{route.provider_name} closed the stream with no data")
                last_error = exc
                is_last_attempt = attempt == policy["max_attempts"] - 1
                if is_last_attempt:
                    break
                total_retries += 1
                await asyncio.sleep(backoff)
                backoff *= policy["backoff_multiplier"]
                continue
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

    if last_error is None:
        last_error = UpstreamError("no provider candidate could be reached before the dispatch deadline")
    last_error.retries = total_retries
    raise last_error
