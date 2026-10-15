"""Independent cardinality and ranking oracles for consecutive-run capacity."""

from itertools import combinations, product
import random

import pytest

from loto_enterprise.core.ranking import _max_addable, limit_consecutive_run


def _valid(nums, limit):
    """Independent oracle: no complete forbidden interval is present."""
    nums = set(nums)
    return all(not set(range(n, n + limit + 1)) <= nums for n in nums)


def _brute_capacity(chosen, optional, limit):
    optional = sorted(optional - chosen)
    for size in range(len(optional), -1, -1):
        if any(_valid(chosen | set(extra), limit)
               for extra in combinations(optional, size)):
            return size
    return -1


@pytest.mark.parametrize("universe", [
    tuple(range(1, 8)),
    (-5, -4, -3, -1, 0, 1, 2),
    (-101, -100, 0, 1, 2, 30, 31),
])
def test_capacity_matches_exhaustive_subset_oracle(universe):
    for states in product(range(3), repeat=len(universe)):
        chosen = {n for n, state in zip(universe, states) if state == 1}
        optional = {n for n, state in zip(universe, states) if state == 2}
        for limit in (1, 2, 3):
            assert _max_addable(chosen, optional, limit) == _brute_capacity(
                chosen, optional, limit
            ), (chosen, optional, limit)


@pytest.mark.parametrize("chosen,optional,limit,expected", [
    ({2, 3, 4}, {1, 5}, 2, -1),
    ({2, 3}, {1, 4, 5}, 2, 1),
    ({2, 4}, {1, 3, 5}, 2, 2),
    ({1, 2}, {2, 3, 4, 6}, 2, 2),  # overlap stays mandatory, never counted twice
    ({-5, -4, 10, 11}, {-6, -3, 9, 12, 14}, 2, 1),
    (set(), set(), 2, 0),
    (set(), {1, 2, 3}, 0, 0),
    ({1}, {2, 3}, 0, -1),
])
def test_capacity_handles_forced_conflicts_gaps_and_overlap(chosen, optional, limit, expected):
    assert _max_addable(chosen, optional, limit) == expected


def test_capacity_does_not_depend_on_translation_or_reflection():
    chosen, optional = {2, 3, 8}, {1, 4, 5, 6, 7, 9, 10}
    expected = _brute_capacity(chosen, optional, 2)
    for sign, offset in product((-1, 1), (-1000, 0, 1000)):
        assert _max_addable({sign * x + offset for x in chosen},
                            {sign * x + offset for x in optional}, 2) == expected


def _brute_selection(ranked, k, requested):
    k = max(0, min(k, len(ranked)))
    if requested <= 0 or k == 0:
        return ranked[:k], 0, []
    for applied in range(requested, max(requested, len(ranked)) + 1):
        # combinations of rank positions are already lexicographically ordered.
        for positions in combinations(range(len(ranked)), k):
            pool = [ranked[i] for i in positions]
            if _valid(pool, applied):
                skipped = [n for i, n in enumerate(ranked[:positions[-1]])
                           if i not in positions]
                return pool, applied, skipped
    raise AssertionError("A finite set must fit when the limit equals its size")


def test_selection_matches_lexicographic_oracle_including_relaxation_and_skips():
    rng = random.Random(20261016)
    for universe in (list(range(1, 10)), [-7, -6, -5, -1, 0, 1, 2, 8, 9]):
        for _ in range(50):
            ranked = universe[:]
            rng.shuffle(ranked)
            for k in (0, 3, 6, 9, 12):
                for limit in (0, 1, 2, 3):
                    assert limit_consecutive_run(ranked, k, limit) == _brute_selection(
                        ranked, k, limit
                    )
