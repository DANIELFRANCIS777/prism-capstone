from app.routing.aliases import ResolvedRoute
from app.routing.dispatch import dispatch_streaming


class _EmptyStreamAdapter:
    """Accepts the connection but yields zero lines - the real-world failure
    mode this regression test targets (distinct from a timeout or 4xx/5xx)."""

    name = "empty"

    async def stream_chat_completion(self, model, messages, timeout):
        return
        yield  # pragma: no cover - makes this an async generator function


class _WorkingStreamAdapter:
    name = "working"

    async def stream_chat_completion(self, model, messages, timeout):
        yield 'data: {"id": "1"}'
        yield "data: [DONE]"


async def test_stream_that_closes_with_zero_lines_fails_over(monkeypatch):
    """Regression test: a candidate whose stream closes immediately (zero SSE
    lines, StopAsyncIteration on the very first read) used to be returned as
    a *successful* open instead of being retried/failed over to the next
    candidate in the chain."""
    adapters = {"empty": _EmptyStreamAdapter(), "working": _WorkingStreamAdapter()}
    monkeypatch.setattr("app.routing.dispatch.get_provider", lambda name: adapters[name])
    monkeypatch.setattr(
        "app.routing.dispatch.retry_policy",
        lambda: {"max_attempts": 1, "initial_backoff_ms": 1, "backoff_multiplier": 1},
    )

    chain = [
        ResolvedRoute(provider_name="empty", model="empty-model"),
        ResolvedRoute(provider_name="working", model="working-model"),
    ]
    handle = await dispatch_streaming(chain, messages=[{"role": "user", "content": "hi"}], timeout=5)

    assert handle.route.provider_name == "working"
    assert handle.fallback is True
