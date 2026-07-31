import pytest

from app.config import validate_pricing_coverage
from app.routing.pricing import compute_cost_usd


def test_cost_from_known_model_and_prices():
    # alpha-small: input $0.15/1M, output $0.60/1M (data/model_pricing.json)
    cost = compute_cost_usd("alpha-small", prompt_tokens=1_000_000, completion_tokens=1_000_000)
    assert cost == pytest.approx(0.15 + 0.60)


def test_cost_scales_linearly_with_tokens():
    # alpha-large: input $3.00/1M -> half the tokens should be half the cost
    cost = compute_cost_usd("alpha-large", prompt_tokens=500_000, completion_tokens=0)
    assert cost == pytest.approx(1.50)


def test_zero_tokens_costs_zero():
    assert compute_cost_usd("alpha-small", 0, 0) == 0.0


def test_unknown_model_costs_zero_not_an_error():
    assert compute_cost_usd("no-such-model", 100, 100) == 0.0


def test_cost_uses_provider_reported_tokens_not_something_client_supplied():
    """There is no client-facing parameter here at all - compute_cost_usd only
    ever takes the token counts the caller passes in, and those must come from
    the provider's `usage` object (see app/routers/chat.py). This test pins
    the function's only inputs so a future change can't accidentally widen it
    to accept a client-declared cost/token override."""
    import inspect

    params = list(inspect.signature(compute_cost_usd).parameters)
    assert params == ["model", "prompt_tokens", "completion_tokens"]


def test_pricing_coverage_passes_when_every_reachable_model_is_priced():
    validate_pricing_coverage(
        reachable_models={"alpha-small", "beta-small"},
        priced_models={"alpha-small", "alpha-large", "beta-small", "beta-large"},
    )


def test_pricing_coverage_fails_loudly_on_an_unpriced_reachable_model():
    """Regression test: an unpriced-but-reachable model must fail startup
    instead of silently metering real usage as free (see compute_cost_usd's
    price-is-None branch)."""
    with pytest.raises(RuntimeError, match="gamma-small"):
        validate_pricing_coverage(
            reachable_models={"alpha-small", "gamma-small"},
            priced_models={"alpha-small"},
        )
