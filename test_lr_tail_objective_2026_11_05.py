"""Independent small-universe checks of the exploratory LR tail objective."""

from itertools import combinations
from math import prod

import pytest

from covering.lr_schemes import LRScheme, lr_schemes
from scripts.analysis.lr_tail_objective_2026_11_05 import LRTailObjective, evaluate_tail


def _brute(scheme, pool, weights, draw_n):
    ordered = sorted(pool)
    rows = [{ordered[p - 1] for p in row} for row in scheme.variants]
    numerator5 = numerator6 = total = 0.0
    for draw in combinations(weights, draw_n):
        mass = prod(weights[n] for n in draw)
        best = max(len(set(draw) & row) for row in rows)
        total += mass
        numerator5 += mass * (best >= 5)
        numerator6 += mass * (best >= 6)
    return numerator5 / total, numerator6 / total


SCHEMES = [
    LRScheme("6/49", 1, 9, ((1, 2, 3, 4, 5, 6), (4, 5, 6, 7, 8, 9))),
    LRScheme("6/49", 2, 7, ((1, 2, 3, 4, 5, 6), (1, 2, 3, 4, 5, 7))),
    LRScheme("5/40", 3, 7, ((1, 2, 3, 4, 5), (1, 2, 3, 4, 6), (2, 3, 4, 6, 7))),
    LRScheme("joker", 4, 7, ((1, 2, 3, 4, 5), (1, 2, 3, 4, 6), (2, 3, 4, 6, 7))),
]


@pytest.mark.parametrize("scheme", SCHEMES)
@pytest.mark.parametrize("zero_weights", [False, True])
@pytest.mark.parametrize("reversed_pool", [False, True])
def test_exact_objective_matches_exhaustive_weighted_draws(scheme, zero_weights, reversed_pool):
    weights = {n: (0.0 if zero_weights and n in (2, 8) else 0.2 + ((n * 17) % 11))
               for n in range(1, 11)}
    pool = list(range(1, scheme.n + 1))
    if reversed_pool:
        pool.reverse()
    draw_n = 5 if scheme.game == "joker" else 6
    expected5, expected6 = _brute(scheme, pool, weights, draw_n)
    actual = evaluate_tail(scheme, pool, weights)
    assert actual["p5plus"] == pytest.approx(expected5, abs=2e-15)
    assert actual["p6"] == pytest.approx(expected6, abs=2e-15)
    assert actual["p5_joker"] is None


@pytest.mark.parametrize("game,n,code,universe,expected5,expected6", [
    ("6/49", 16, 59, 49, 29008 / 13983816, 112 / 13983816),
    ("joker", 15, 12, 45, 118 / 1221759, 0),
    ("5/40", 19, 48, 40, 31473 / 3838380, 0),
])
def test_official_uniform_odds_and_terminal_mapping(game,n,code,universe,expected5,expected6):
    scheme = next(s for s in lr_schemes(game,n) if s.code == code)
    objective = LRTailObjective(scheme, {i: 1 for i in range(1, universe + 1)})
    for pool in [list(range(1,n+1)), list(range(universe-n+1,universe+1))]:
        value = objective.evaluate(pool)
        assert value["p5plus"] == pytest.approx(expected5, abs=1e-16)
        assert value["p6"] == pytest.approx(expected6, abs=1e-16)


def test_uniform_weights_cannot_create_pool_selection_advantage():
    scheme = SCHEMES[1]
    objective = LRTailObjective(scheme, {i: 1 for i in range(1, 11)})
    values = [objective.evaluate(pool) for pool in combinations(range(1,11),scheme.n)]
    assert all(value == values[0] for value in values)


def test_weight_scale_does_not_change_probabilities():
    scheme = SCHEMES[0]
    weights = {n: 0.5 + n for n in range(1, 11)}
    a = evaluate_tail(scheme, range(1,10), weights)
    b = evaluate_tail(scheme, range(1,10), {n: w * 1e200 for n,w in weights.items()})
    assert a == pytest.approx(b)


def test_joker_requires_an_explicit_independent_second_urn_probability():
    scheme = SCHEMES[3]
    objective = LRTailObjective(scheme, {i: 1 for i in range(1, 11)}, joker_probability=0.05)
    result = objective.evaluate(range(1,8))
    assert result["p5_joker"] == pytest.approx(result["p5plus"] / 20)
    assert result["p6"] == 0


@pytest.mark.parametrize("weight", [-1, float("inf"), float("nan"), True])
def test_invalid_weights_are_rejected(weight):
    weights = {n: 1 for n in range(1, 11)}
    weights[3] = weight
    with pytest.raises(ValueError):
        LRTailObjective(SCHEMES[0], weights)


@pytest.mark.parametrize("pool", [range(1,9), [1]*9, range(20,29)])
def test_invalid_pools_are_rejected(pool):
    objective = LRTailObjective(SCHEMES[0], {n: 1 for n in range(1, 11)})
    with pytest.raises(ValueError):
        objective.evaluate(pool)


def test_duplicate_rows_are_rejected_instead_of_counted_as_extra_coverage():
    row = (1,2,3,4,5,6)
    with pytest.raises(ValueError):
        LRTailObjective(LRScheme("6/49",1,7,(row,row)), {n:1 for n in range(1,11)})


@pytest.mark.parametrize("scheme", SCHEMES)
@pytest.mark.parametrize("batch_size", [1, 3, 16])
def test_batched_objective_matches_independent_draw_enumeration(scheme, batch_size):
    weights = {n: 0.3 + (n * 7) % 9 for n in range(1,11)}
    pools = list(combinations(range(1,11),scheme.n))[:17]
    objective = LRTailObjective(scheme,weights)
    values = objective.evaluate_many(pools,batch_size=batch_size)
    for pool,value in zip(pools,values):
        assert tuple(value) == pytest.approx(_brute(scheme,pool,weights,objective.draw_n), abs=2e-15)


def test_tiny_outside_mass_is_not_lost_by_subtraction():
    scheme = LRScheme("6/49",1,7,((1,2,3,4,5,6),))
    weights = {n: (1.0 if n <= 5 else 0.0 if n == 6 else 1e-20) for n in range(1,10)}
    value = evaluate_tail(scheme,range(1,8),weights)
    assert value["p5plus"] == pytest.approx(1.0,abs=1e-15)
    assert value["p6"] == 0.0
