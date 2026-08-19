from app.routing.aliases import ResolvedRoute
from app.routing.dispatch import dispatch_non_streaming


class _RecordingAdapter:
    """Records which api_key it was constructed with, so the test can prove
    that's what actually reached the outbound call site - not just that
    get_provider() was asked for one."""

    def __init__(self, name: str, api_key: str | None = None):
        self.name = name
        self.api_key = api_key

    async def chat_completion(self, model, messages, timeout):
        return {
            "id": "x",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            "_served_with_api_key": self.api_key,
        }


def _fake_get_provider(name, api_key_override=None):
    return _RecordingAdapter(name, api_key_override)


async def test_no_credential_reaches_dispatch_unchanged(monkeypatch):
    """provider_credentials=None (every seeded/operator key, and a self-serve
    org with no BYOK credential for this provider) must dispatch with no
    override - the exact pre-BYOK call shape."""
    monkeypatch.setattr("app.routing.dispatch.get_provider", _fake_get_provider)
    monkeypatch.setattr(
        "app.routing.dispatch.retry_policy",
        lambda: {"max_attempts": 1, "initial_backoff_ms": 1, "backoff_multiplier": 1},
    )

    chain = [ResolvedRoute(provider_name="alpha", model="alpha-small")]
    result = await dispatch_non_streaming(chain, [{"role": "user", "content": "hi"}], timeout=5)

    assert result.body["_served_with_api_key"] is None


async def test_orgs_own_credential_is_what_reaches_the_adapter(monkeypatch):
    """The whole point of BYOK: when the org has a stored credential for the
    resolved provider, dispatch must use it, not the platform's shared key."""
    monkeypatch.setattr("app.routing.dispatch.get_provider", _fake_get_provider)
    monkeypatch.setattr(
        "app.routing.dispatch.retry_policy",
        lambda: {"max_attempts": 1, "initial_backoff_ms": 1, "backoff_multiplier": 1},
    )

    chain = [ResolvedRoute(provider_name="alpha", model="alpha-small")]
    result = await dispatch_non_streaming(
        chain,
        [{"role": "user", "content": "hi"}],
        timeout=5,
        provider_credentials={"alpha": "sk-orgs-own-alpha-key"},
    )

    assert result.body["_served_with_api_key"] == "sk-orgs-own-alpha-key"


async def test_credential_for_a_different_provider_does_not_leak_across(monkeypatch):
    """An org's beta credential must never be used for an alpha call - the
    dict is keyed per provider name, checked at each candidate in the chain."""
    monkeypatch.setattr("app.routing.dispatch.get_provider", _fake_get_provider)
    monkeypatch.setattr(
        "app.routing.dispatch.retry_policy",
        lambda: {"max_attempts": 1, "initial_backoff_ms": 1, "backoff_multiplier": 1},
    )

    chain = [ResolvedRoute(provider_name="alpha", model="alpha-small")]
    result = await dispatch_non_streaming(
        chain,
        [{"role": "user", "content": "hi"}],
        timeout=5,
        provider_credentials={"beta": "sk-orgs-own-beta-key"},
    )

    assert result.body["_served_with_api_key"] is None
