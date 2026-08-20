import pytest

from app.semantic import cosine_similarity, term_frequency, tokenize


def test_identical_text_has_perfect_similarity():
    a = term_frequency("How do I reset my password on the dashboard?")
    b = term_frequency("How do I reset my password on the dashboard?")
    assert cosine_similarity(a, b) == pytest.approx(1.0)


def test_paraphrase_with_shared_vocabulary_scores_high():
    a = term_frequency("How do I reset my password on the dashboard?")
    b = term_frequency("What are the steps to reset my dashboard password?")
    assert cosine_similarity(a, b) >= 0.9


def test_near_miss_scores_lower_than_true_paraphrase():
    original = term_frequency("How do I reset my password on the dashboard?")
    paraphrase = term_frequency("What are the steps to reset my dashboard password?")
    near_miss = term_frequency("How do I reset my two-factor authentication on the dashboard?")
    assert cosine_similarity(original, near_miss) < cosine_similarity(original, paraphrase)


def test_unrelated_text_has_low_similarity():
    a = term_frequency("How do I reset my password?")
    b = term_frequency("Compare TCP and UDP for game servers.")
    assert cosine_similarity(a, b) < 0.3


def test_empty_vector_has_zero_similarity():
    assert cosine_similarity(term_frequency(""), term_frequency("anything")) == 0.0


def test_scaffolding_and_stop_words_are_filtered_out():
    tokens = tokenize("What are the steps to do this?")
    assert "what" not in tokens
    assert "steps" not in tokens
    assert "the" not in tokens


def test_contraction_scores_identically_to_its_uncontracted_form():
    """Regression test: _TOKEN_RE has no apostrophe in its character class,
    so "what's" splits into "what" (a stopword) + a spurious leftover "s"
    token that used to survive filtering and dilute the score against the
    uncontracted phrasing - 0.816 instead of ~1.0, below every configured
    cache threshold. A tenant asking the identical question with a
    contraction should not pay for a fresh upstream call."""
    a = term_frequency("What is a message queue?")
    b = term_frequency("What's a message queue?")
    assert cosine_similarity(a, b) == pytest.approx(1.0)
