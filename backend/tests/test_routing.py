import pytest

from app.routing.aliases import RouteNotImplementedError, UnknownModelError, resolve_candidate_chain
from app.routing.auto_router import classify_difficulty


def test_alias_resolves_to_primary_then_ordered_fallbacks():
    chain = resolve_candidate_chain("fast")
    assert [c.model for c in chain] == ["alpha-small", "beta-small"]
    assert [c.provider_name for c in chain] == ["alpha", "beta"]


def test_smart_alias_resolves_correctly_too():
    chain = resolve_candidate_chain("smart")
    assert [c.model for c in chain] == ["alpha-large", "beta-large"]


def test_literal_model_name_is_a_single_candidate_chain():
    chain = resolve_candidate_chain("alpha-large")
    assert len(chain) == 1
    assert chain[0].provider_name == "alpha"


def test_unknown_model_raises_unknown_model_error():
    with pytest.raises(UnknownModelError):
        resolve_candidate_chain("no-such-model")


def test_auto_alias_is_not_resolved_by_resolve_candidate_chain_directly():
    """`auto` needs classification first (app/routing/auto_router.py) - it has
    no static `primary`, so asking for it here specifically must not silently
    resolve to something."""
    with pytest.raises(RouteNotImplementedError):
        resolve_candidate_chain("auto")


# --- auto router classification --------------------------------------------


def test_short_but_hard_proof_routes_to_smart():
    tier, _ = classify_difficulty("Prove that the square root of 2 is irrational.")
    assert tier == "smart"


def test_long_but_trivial_lookup_routes_to_fast():
    prompt = (
        "Here is our on-call roster for the next two weeks: Monday - Priya, "
        "Tuesday - Chen, Wednesday - Amara, Thursday - Diego. Who is on call this Thursday?"
    )
    tier, _ = classify_difficulty(prompt)
    assert tier == "fast"


def test_conflicting_signals_resolved_by_count_not_first_match():
    # Contains both a "fast" signal (divisible by) and two "smart" signals
    # (prove, disprove) - smart must win because it has more matches.
    tier, reason = classify_difficulty(
        "Prove or disprove: the sum of any five consecutive integers is divisible by 5."
    )
    assert tier == "smart"
    assert "prove" in reason and "disprove" in reason


def test_classifier_beats_length_only_baseline_on_full_eval_set():
    """The spec: a length-only heuristic scores ~60% on data/routing_eval.jsonl
    because it's deliberately trapped. This is the regression guard tying the
    classifier to that actual grading artifact."""
    import json
    from pathlib import Path

    eval_file = Path(__file__).resolve().parents[2] / "data" / "routing_eval.jsonl"
    cases = [json.loads(line) for line in eval_file.read_text().splitlines() if line.strip()]

    correct = sum(1 for c in cases if classify_difficulty(c["prompt"])[0] == c["expected_tier"])
    accuracy = correct / len(cases)
    assert accuracy >= 0.9
