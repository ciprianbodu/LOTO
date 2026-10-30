"""Independent exact-count checks for the native path used on normal pools."""

import random
from itertools import combinations
from math import comb

import pytest

from covering.probability import wheel_hit_profile


def _brute_profile(pool, tickets):
    """Enumerate actual intersections, independently of subset propagation."""
    v = len(pool)
    ticket_sets = [set(ticket) for ticket in tickets]
    counts = [[0] * (v + 1) for _ in range(v + 1)]
    for size in range(v + 1):
        for subset in combinations(pool, size):
            subset = set(subset)
            best = max((len(subset & t) for t in ticket_sets), default=0)
            for threshold in range(best + 1):
                counts[size][threshold] += 1
    return tuple(tuple(row) for row in counts)


@pytest.mark.parametrize("v", [8, 9, 12, 16])
@pytest.mark.parametrize("kind", ["empty", "one", "mixed"])
def test_profile_matches_every_subset_intersection(v, kind):
    # Irregular labels and rank order exercise the public positional mapping.
    pool = list(range(2, 2 * v + 1, 2))[::-1]
    rng = random.Random(314 + v)
    if kind == "empty":
        tickets = []
    elif kind == "one":
        tickets = [pool[:5]]
    else:
        tickets = [rng.sample(pool, n) for n in (1, 3, 5, 6)]
        tickets += [list(reversed(tickets[-1]))]  # Repeated ticket, other order.
    assert wheel_hit_profile(pool, tickets) == _brute_profile(pool, tickets)


@pytest.mark.parametrize("v,pick", [(9, 5), (12, 6), (16, 5)])
def test_full_system_counts_have_closed_form(v, pick):
    pool = list(range(1, v + 1))
    tickets = list(combinations(pool, pick))
    expected = tuple(
        tuple(comb(v, size) if threshold <= min(size, pick) else 0
              for threshold in range(v + 1))
        for size in range(v + 1)
    )
    assert wheel_hit_profile(pool, tickets) == expected
