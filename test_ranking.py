"""rank_by_score — scoruri finite + sărire NaN/inf (tie-break determinist)."""
from __future__ import annotations

from loto_enterprise.core.ranking import rank_by_score


def test_rank_finite_highest_first():
    assert rank_by_score({1: 0.2, 2: 0.9, 3: 0.05}, 2) == [2, 1]


def test_rank_tie_breaks_on_larger_number():
    assert rank_by_score({1: 0.5, 5: 0.5}, 2) == [5, 1]


def test_rank_skips_nan_and_inf():
    scores = {1: 0.5, 2: float("nan"), 3: 0.9, 4: float("inf"), 5: 0.1}
    assert rank_by_score(scores, 3) == [3, 1, 5]


def test_rank_all_nan_returns_empty():
    assert rank_by_score({1: float("nan"), 2: float("nan")}, 2) == []


def test_rank_empty_or_k_zero():
    assert rank_by_score({}, 5) == []
    assert rank_by_score({1: 1.0}, 0) == []
