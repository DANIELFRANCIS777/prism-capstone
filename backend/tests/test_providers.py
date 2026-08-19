from app.providers import get_provider, provider_registry


def test_no_override_returns_the_cached_singleton_adapter():
    """The no-BYOK-credential path (every seeded/operator-provisioned key,
    and any self-serve org that hasn't added a credential yet) must be
    byte-for-byte identical to pre-BYOK behavior: the same cached adapter
    instance, not a freshly constructed one."""
    adapter = get_provider("alpha")
    assert adapter is provider_registry()["alpha"]


def test_override_returns_a_fresh_adapter_with_the_overriding_key():
    base = provider_registry()["alpha"]
    adapter = get_provider("alpha", api_key_override="sk-tenant-own-key")

    assert adapter is not base
    assert adapter.api_key == "sk-tenant-own-key"
    assert adapter.name == base.name
    assert adapter.base_url == base.base_url


def test_override_does_not_mutate_the_cached_singleton():
    get_provider("alpha", api_key_override="sk-tenant-own-key")
    assert provider_registry()["alpha"].api_key != "sk-tenant-own-key"
