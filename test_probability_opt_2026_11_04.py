"""Exact probabilities checked against physical subset/draw enumeration."""

import random
from itertools import combinations
from math import comb

import pytest

from covering.probability import (
    _hit_counts,
    _hit_counts_numpy,
    wheel_hit_probabilities,
    wheel_hit_profile,
)


def _brute_profile(pool, tickets):
    ticket_sets = list(map(set, tickets))
    counts = [[0] * (len(pool) + 1) for _ in range(len(pool) + 1)]
    for size in range(len(pool) + 1):
        for drawn in combinations(pool, size):
            drawn = set(drawn)
            hits = max((len(drawn & t) for t in ticket_sets), default=0)
            for threshold in range(hits + 1):
                counts[size][threshold] += 1
    return tuple(map(tuple, counts))


@pytest.mark.parametrize("v", [1, 2, 4, 6, 8, 9, 10, 12])
@pytest.mark.parametrize("seed", range(16))
def test_every_subset_against_physical_intersection(v, seed):
    rng = random.Random(20261104 + 100 * v + seed)
    pool = rng.sample(range(2, 100, 3), v)
    count = (0, 1, 3, 15)[seed % 4]
    tickets = [rng.sample(pool, rng.randint(1, v)) for _ in range(count)]
    if tickets:
        tickets.append(list(reversed(tickets[0])))
    expected = _brute_profile(pool, tickets)
    assert wheel_hit_profile(pool, tickets) == expected
    assert wheel_hit_profile(list(reversed(pool)), tickets) == expected


@pytest.mark.parametrize("seed", range(120))
def test_every_full_draw_matches_probabilities(seed):
    rng = random.Random(91004 + seed)
    universe = 8 + seed % 5
    v = 3 + seed % (universe - 2)
    pool = rng.sample(range(1, universe + 1), v)
    draw_n = (5, 6)[seed % 2]
    pick = min(v, (5, 6)[(seed // 2) % 2])
    tickets = [rng.sample(pool, pick) for _ in range(seed % 6)]
    actual = wheel_hit_probabilities(pool, tickets, draw_n, universe)
    pool_set = set(pool)
    ticket_sets = list(map(set, tickets))
    pool_counts, ticket_counts = [0] * (draw_n + 1), [0] * (draw_n + 1)
    for drawn in combinations(range(1, universe + 1), draw_n):
        drawn = set(drawn)
        pool_hits = len(drawn & pool_set)
        ticket_hits = max((len(drawn & t) for t in ticket_sets), default=0)
        for threshold in range(1, draw_n + 1):
            pool_counts[threshold] += pool_hits >= threshold
            ticket_counts[threshold] += ticket_hits >= threshold
    denominator = comb(universe, draw_n)
    assert actual == {
        "pool": {t: pool_counts[t] / denominator for t in range(1, draw_n + 1)},
        "ticket": {t: ticket_counts[t] / denominator for t in range(1, draw_n + 1)},
    }


@pytest.mark.parametrize("v", range(1, 21))
@pytest.mark.parametrize("pick_selector", [0, 1, 3, 5, 6, 20])
def test_single_ticket_and_empty_wheel_closed_form_against_subset_dp(v, pick_selector):
    pick = min(v, pick_selector)
    masks = ((1 << pick) - 1,) if pick else ()
    # Native subset propagation is a second, independent algorithm and keeps
    # the largest supported pool sizes covered without expensive set loops.
    actual = _hit_counts.__wrapped__(v, masks)
    assert actual == _hit_counts_numpy(v, masks)
    assert all(row[0] == comb(v, size) for size, row in enumerate(actual))
    assert all(row[t] == 0 for row in actual for t in range(pick + 1, v + 1))


@pytest.mark.parametrize("universe,draw_n,pick", [(49, 6, 6), (40, 6, 5), (45, 5, 5)])
@pytest.mark.parametrize("v", [6, 9, 12, 16, 19, 20])
def test_single_ticket_real_game_hypergeometric_baseline(universe, draw_n, pick, v):
    pool = list(range(1, v + 1))
    tickets = [pool[:pick], list(reversed(pool[:pick]))]
    actual = wheel_hit_probabilities(pool, tickets, draw_n, universe)
    for threshold in range(1, draw_n + 1):
        numerator = sum(
            comb(pick, hits) * comb(universe - pick, draw_n - hits)
            for hits in range(threshold, min(draw_n, pick) + 1)
        )
        assert actual["ticket"][threshold] == numerator / comb(universe, draw_n)
    if pick == 5 and draw_n == 6:
        assert actual["ticket"][6] == 0


@pytest.mark.parametrize("seed", range(24))
def test_adding_ticket_never_reduces_any_threshold(seed):
    rng = random.Random(371 + seed)
    v = 9 + seed % 12
    pool = list(range(1, v + 1))
    tickets = [rng.sample(pool, 5) for _ in range(10)]
    before = wheel_hit_profile(pool, tickets)
    after = wheel_hit_profile(pool, tickets + [rng.sample(pool, 6)])
    assert all(a >= b for ar, br in zip(after, before) for a, b in zip(ar, br))


@pytest.mark.parametrize(
    "pool,tickets",
    [
        ([1, 1], [[1]]),
        ([1, 2], [[1, 1]]),
        ([1, 2], [[3]]),
        ([1, 2], [[]]),
        ([1, 2.5], [[1]]),
        ([1, 2], [[float("nan")]]),
        ([1, 2], [[float("inf")]]),
        ([], []),
        (list(range(21)), [[1]]),
    ],
)
def test_invalid_numbers_are_rejected_before_shortcuts(pool, tickets):
    with pytest.raises(ValueError):
        wheel_hit_profile(pool, tickets)
