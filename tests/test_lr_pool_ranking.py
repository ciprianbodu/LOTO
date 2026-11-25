"""Strict LR pools preserve rank whenever a full admissible selection exists."""

from itertools import combinations
from random import Random

import pytest

from covering.lr_pool_ranking import lr_pool_capacity, rank_lr_pool, select_ranked_lr_pool


def _no_triple(numbers):
    selected = set(numbers)
    return not any({n + 1, n + 2} <= selected for n in selected)


@pytest.mark.parametrize(
    ("ranked", "replacement", "skipped"),
    [
        ([13, 20, 21, 26, 27, 28, 30, 32, 33, 40, 34, 35], 35, 28),
        ([4, 5, 9, 12, 23, 24, 25, 32, 36, 43, 6, 44], 44, 25),
    ],
)
def test_weakest_rank_in_the_reported_triples_yields_to_next_feasible_number(ranked, replacement, skipped):
    expected = [number for number in ranked[:10] if number != skipped] + [replacement]
    pool = select_ranked_lr_pool(ranked, 10)
    assert pool == expected
    assert _no_triple(pool)


def test_score_ties_keep_canonical_descending_ball_order_and_allow_pairs():
    assert rank_lr_pool(dict.fromkeys(range(1, 9), 1.0), 5) == [8, 7, 5, 4, 2]
    assert select_ranked_lr_pool([40, 10, 20, 30, 41, 5], 5) == [40, 10, 20, 30, 41]


def test_best_legal_rank_is_skipped_only_when_it_blocks_completion():
    # Keeping the first ball 3 would make any full pool of four impossible.
    assert select_ranked_lr_pool([3, 1, 2, 4, 5], 4) == [1, 2, 4, 5]
    assert select_ranked_lr_pool([3, 1, 2, 4, 5], 3) == [3, 1, 4]


def test_capacity_uses_actual_labels_and_never_relaxes_or_shortens():
    assert lr_pool_capacity(range(10, 41)) == 21
    assert lr_pool_capacity(range(1, 50)) == 33
    assert lr_pool_capacity([10, 11, 12, 14, 15, 16, 17, 20]) == 6
    with pytest.raises(ValueError, match="cel mult 6 din cele 8.*necesită 7"):
        select_ranked_lr_pool([10, 11, 12, 14, 15, 16, 17, 20], 7)
    with pytest.raises(ValueError, match="cel mult 2 din cele 2.*necesită 3"):
        rank_lr_pool({1: 10, 2: float("nan"), 3: 5}, 3)
    assert select_ranked_lr_pool([], 0) == []


def test_dense_and_gapped_windows_match_exhaustive_rank_order():
    rng = Random(171)
    for _ in range(35):
        ranked = rng.sample(range(22), 10)
        k = rng.randint(4, 8)
        feasible = [list(t) for t in combinations(ranked, k) if _no_triple(t)]
        if feasible:
            assert select_ranked_lr_pool(ranked, k) == feasible[0]
        else:
            with pytest.raises(ValueError):
                select_ranked_lr_pool(ranked, k)
