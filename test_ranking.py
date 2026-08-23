"""Ranking canonic: finite neschimbate; NaN nu mai depinde de ordinea de inserare."""
from __future__ import annotations

from loto_enterprise.core.pool_selection import select_pool_from_scores
from loto_enterprise.core.ranking import rank_by_score


def test_finite_scores_highest_first_then_number_desc():
    scores = {1: 0.5, 2: 0.9, 3: 0.9, 4: 0.1}
    assert rank_by_score(scores, 3) == [3, 2, 1]


def test_nan_ranks_last_independent_of_insertion_order():
    a = {1: float("nan"), 2: 0.4, 3: 0.8}
    b = {3: 0.8, 2: 0.4, 1: float("nan")}
    assert rank_by_score(a, 2) == [3, 2]
    assert rank_by_score(b, 2) == [3, 2]


def test_all_nan_falls_back_to_number_desc_deterministically():
    # Toate NaN → cheie −inf pentru toți → tie-break pe număr desc.
    a = {1: float("nan"), 5: float("nan"), 3: float("nan")}
    b = {5: float("nan"), 3: float("nan"), 1: float("nan")}
    assert rank_by_score(a, 3) == [5, 3, 1]
    assert rank_by_score(b, 3) == [5, 3, 1]


def test_select_pool_skips_nonfinite_and_keeps_finite_top():
    scores = {1: 0.1, 2: float("nan"), 3: 0.9, 4: float("inf"), 5: 0.5}
    pool = select_pool_from_scores(scores, pool_size=2, blacklist=set(), max_num=5)
    assert pool == [3, 5]
