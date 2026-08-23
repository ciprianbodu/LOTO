"""Ranking canonic: finite neschimbate; NaN nu mai depinde de ordinea de inserare."""
from __future__ import annotations

from loto_enterprise.core.pool_selection import complete_pool, select_pool_from_scores
from loto_enterprise.core.ranking import is_finite_score, rank_by_score


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


def test_is_finite_score_rejects_nan_inf_and_junk():
    assert is_finite_score(0.0) and is_finite_score(1.5)
    assert not is_finite_score(float("nan"))
    assert not is_finite_score(float("inf"))
    assert not is_finite_score(float("-inf"))
    assert not is_finite_score("x")
    assert not is_finite_score(None)


def test_select_pool_skips_nonfinite_and_keeps_finite_top():
    scores = {1: 0.1, 2: float("nan"), 3: 0.9, 4: float("inf"), 5: 0.5}
    pool = select_pool_from_scores(scores, pool_size=2, blacklist=set(), max_num=5)
    assert pool == [3, 5]


def test_complete_pool_noop_when_already_full():
    pool = [3, 5, 8]
    out = complete_pool(pool, 3, max_num=10, scores={3: 0.9, 5: 0.8, 8: 0.7, 10: 0.1})
    assert out == [3, 5, 8]


def test_complete_pool_fills_allowed_not_blacklist():
    """Scoruri rare: umple din universul permis, NU din blacklist (bug vechi)."""
    scores = {4: 0.9, 5: 0.8, 6: 0.1}  # 4,5 sunt în blacklist
    blacklist = {4, 5, 6}
    out = complete_pool(
        [7, 8], 5,
        max_num=10,
        scores=scores,
        exclude=blacklist,
        freq={10: 3.0, 9: 2.0, 3: 1.0, 4: 99.0},
        allow_excluded_last_resort=False,
    )
    assert len(out) == 5
    assert 7 in out and 8 in out
    assert not (set(out) & blacklist)
    assert 10 in out  # cea mai mare frecvență permisă


def test_complete_pool_last_resort_only_when_allowed_exhausted():
    scores = {1: 0.9, 2: 0.8}
    out = complete_pool(
        [1], 3,
        max_num=3,
        scores=scores,
        exclude={2, 3},
        freq={2: 10.0, 3: 9.0},
        allow_excluded_last_resort=True,
    )
    assert 1 in out
    assert set(out) <= {1, 2, 3}
    assert len(out) == 3


def test_complete_pool_enforcement_never_readds_exclude():
    out = complete_pool(
        [1], 4,
        max_num=5,
        scores={2: 0.5, 3: 0.4},
        exclude={2, 3, 4, 5},
        freq={2: 9, 3: 8, 4: 7, 5: 6},
        allow_excluded_last_resort=False,
    )
    assert out == [1]
