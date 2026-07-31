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
