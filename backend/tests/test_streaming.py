import uuid

from sqlalchemy import select

from app.models import RequestLog
from app.routing.aliases import ResolvedRoute
from app.routing.dispatch import StreamHandle
from app.streaming import forward_stream
from tests.conftest import make_virtual_key


class _FakeStreamGenerator:
    """Stands in for a real upstream stream so the test controls exactly how
    far forward_stream gets before the client "disconnects" - no real HTTP
    connection or OS-level socket timing involved."""

    def __init__(self, lines):
        self._lines = iter(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._lines)
        except StopIteration:
            raise StopAsyncIteration


async def test_client_disconnect_mid_stream_still_bills_and_logs(db):
    """Regression test: forward_stream used to only catch StopAsyncIteration
    and UpstreamError - a real client disconnect (Starlette closes the
    response via GeneratorExit) skipped record_usage/log_request entirely,
    even though the upstream had already generated (and billed) real tokens.
    Usage is parsed from a line *before* it's yielded, specifically so a
    disconnect that happens exactly during the yield of the final,
    usage-bearing chunk still gets it captured."""
    key = make_virtual_key(raw_key=f"test-stream-disconnect-{uuid.uuid4().hex[:8]}")
    db.add(key)
    await db.commit()

    usage_line = (
        'data: {"id": "x", "choices": [{"delta": {}, "finish_reason": "stop"}], '
        '"usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}}'
    )
    handle = StreamHandle(
        generator=_FakeStreamGenerator([usage_line, "data: [DONE]"]),
        first_line='data: {"delta": "hello"}',
        route=ResolvedRoute(provider_name="alpha", model="alpha-small"),
        fallback=False,
        retries=0,
    )

    gen = forward_stream(db, key, "fast", handle)
    await gen.__anext__()  # client receives the first chunk
    await gen.__anext__()  # ...and the usage-bearing chunk (usage now captured)...
    await gen.aclose()  # ...then disconnects before [DONE] arrives

    result = await db.execute(select(RequestLog).where(RequestLog.virtual_key_id == key.id))
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "client_disconnected"
    assert rows[0].prompt_tokens == 5
    assert rows[0].completion_tokens == 7
    assert float(rows[0].cost_usd) > 0


async def test_stream_that_completes_normally_still_logs_ok(db):
    """Sanity check alongside the disconnect test above: the normal
    completion path (client stays connected through [DONE]) is unaffected
    by the disconnect-handling changes."""
    key = make_virtual_key(raw_key=f"test-stream-ok-{uuid.uuid4().hex[:8]}")
    db.add(key)
    await db.commit()

    usage_line = (
        'data: {"id": "x", "choices": [{"delta": {}, "finish_reason": "stop"}], '
        '"usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}}'
    )
    handle = StreamHandle(
        generator=_FakeStreamGenerator([usage_line, "data: [DONE]"]),
        first_line='data: {"delta": "hi"}',
        route=ResolvedRoute(provider_name="alpha", model="alpha-small"),
        fallback=False,
        retries=0,
    )

    chunks = [chunk async for chunk in forward_stream(db, key, "fast", handle)]
    assert any(b"[DONE]" in c for c in chunks)

    result = await db.execute(select(RequestLog).where(RequestLog.virtual_key_id == key.id))
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "ok"
    assert rows[0].prompt_tokens == 3
    assert rows[0].completion_tokens == 4
