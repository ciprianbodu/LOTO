"""Tests for scorer validation shared by benchmark and production."""

from loto_enterprise.core.score_validation import has_usable_score_variance


def test_usable_scores_need_finite_variance():
    assert has_usable_score_variance({1: 0.0, 2: 1.0}) is True
    assert has_usable_score_variance({1: 0.0, 2: 1e-13}) is False
    assert has_usable_score_variance({1: 2.0, 2: 2.0}) is False
    assert has_usable_score_variance({1: 0.0, 2: float("nan")}) is False
    assert has_usable_score_variance({1: 0.0, 2: float("inf")}) is False
    assert has_usable_score_variance({1: 0.0, 2: "invalid"}) is False
    assert has_usable_score_variance({1: 1.0}) is False
    assert has_usable_score_variance({}) is False
