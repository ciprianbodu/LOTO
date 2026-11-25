"""A playable official Joker zone always has one valid second-urn number."""

import pytest

from covering.lr_budget import ANY_PRIZE, FOUR_PLUS, recommend_games
from covering.lr_pool_budget import restrict_to_pool


@pytest.mark.parametrize("objective", (FOUR_PLUS, ANY_PRIZE))
@pytest.mark.parametrize("jokers", ((), None, "12", [False, 4], [0], [21], [4.5], [4, 4]))
def test_joker_layout_rejects_missing_or_invalid_second_urn(objective, jokers):
    plan, = recommend_games(100, objective, "joker")
    with pytest.raises(ValueError, match="Urna 2 Joker"):
        restrict_to_pool(plan, objective, [1, 3, 4, 7, 10, 20, 21], jokers)


@pytest.mark.parametrize("objective", (FOUR_PLUS, ANY_PRIZE))
def test_joker_layout_keeps_real_second_urn_assignment(objective):
    plan, = recommend_games(100, objective, "joker")
    pool = [1, 3, 4, 7, 10, 20, 21]
    layout = restrict_to_pool(plan, objective, pool, [20, 5])
    assert layout["tickets"]
    assert {zone["joker"] for ticket in layout["tickets"] for zone in ticket} <= {20, 5}
    assert layout["attained"] <= layout["upper"]


def _nominal_pool(game):
    return {
        "6/49": (2, 3, 6, 9, 10, 14, 15, 19, 23, 24, 28, 32, 33, 38, 43, 48),
        "5/40": (1, 2, 4, 7, 8, 10, 13, 14, 16, 19, 20, 23, 25, 26, 29, 30, 33, 36, 39),
        "joker": (2, 5, 8, 9, 12, 15, 16, 19, 22, 23, 27, 28, 32, 37, 43),
    }[game]


@pytest.mark.parametrize(
    "game,objective,budget,old_value",
    [("5/40", FOUR_PLUS, 300, 118807), ("joker", ANY_PRIZE, 300, 5022660)],
)
def test_lr_beam_can_improve_without_relaxing_other_prize_thresholds(game, objective, budget, old_value):
    from copy import deepcopy

    from scripts.analysis.compare_lr_portfolio_search import exact

    plan, = recommend_games(budget, objective, game)
    joker = (3, 13) if game == "joker" else ()
    pool = _nominal_pool(game)
    old = exact(restrict_to_pool(deepcopy(plan), objective, pool, joker, time_limit_s=0), pool, joker)
    new = exact(restrict_to_pool(deepcopy(plan), objective, pool, joker, time_limit_s=2), pool, joker)
    assert old["attained"] == old_value
    assert new["attained"] > old["attained"]
    assert new["cost_bani"] == old["cost_bani"]
    assert all(new[t] >= old[t] for t in ("4+", "5+", "6+"))
    assert new["duplicate_paid_variants"] == 0
    assert new["gap"] >= 0


def test_stop_keeps_exact_legacy_incumbent_with_real_6_49_jackpot():
    from copy import deepcopy
    from math import comb

    from scripts.analysis.compare_lr_portfolio_search import exact

    pool = _nominal_pool("6/49")
    plan, = recommend_games(300, FOUR_PLUS, "6/49")
    timed = restrict_to_pool(deepcopy(plan), FOUR_PLUS, pool, time_limit_s=0)
    stopped = restrict_to_pool(deepcopy(plan), FOUR_PLUS, pool, cancelled=lambda: True)
    assert stopped == timed
    results = exact(stopped, pool, ())
    assert results["6+"] == stopped["variants"] - results["duplicate_paid_variants"]
    assert results["denominator"] == comb(49, 6)
    assert stopped["optimal"] == (stopped["attained"] == stopped["upper"])


def test_duplicate_bets_charge_all_variants_but_joker_label_is_part_of_the_bet():
    from covering.lr_pool_budget import _Choice, _PortfolioSearch
    from covering.lr_schemes import lr_schemes
    from loto_enterprise.core.lotteries import require_lottery

    lot = require_lottery("joker", "RO")
    search = _PortfolioSearch(lot, 7, ANY_PRIZE, 10000, 2, 3, None, 1, None)
    scheme = next(s for s in lr_schemes("joker") if s.code == 45)
    zone = search.make_zone(scheme.code, tuple(range(scheme.n)), scheme.size)
    first, second = _Choice(zone, 0), _Choice(zone, 1)
    one = search.portfolio((first,))
    repeat = search.portfolio((first, first))
    alternate = search.portfolio((first, second))
    score, main, _ = search.exact(one)
    assert search.exact(repeat) == (score, main, scheme.size)
    new_score, same_main, duplicates = search.exact(alternate)
    assert new_score > score and same_main == main and duplicates == 0
    assert repeat.price == alternate.price > one.price


@pytest.mark.parametrize("budget,expected_zones", [(599, 2), (600, 3)])
def test_lr_milp_matches_full_enumeration_with_second_ticket_fee_and_protection(budget, expected_zones):
    from functools import reduce
    from itertools import combinations
    from operator import or_

    scipy = pytest.importorskip("scipy.optimize")
    # A wheel or benchmark may already have used the default HiGHS scheduler.
    preliminary = scipy.milp([-1.0], integrality=[1], bounds=scipy.Bounds(0, 1))
    assert preliminary.success
    from covering.lr_pool_milp import solve_zone_coverage
    from covering.lr_portfolio import cost_bani

    paid_size = (2, 2, 1, 3)
    target = (0b0011, 0b0110, 0b1000, 0b0001), (0b0100, 0b0100, 0b0111, 0b1100)
    guard = (0b0000, 0b0010, 0b0000, 0b0000)
    layers = ((0b0111, 2), (0b1000, 3))

    def weighted(values, selected):
        covered = reduce(or_, (values[i] for i in selected), 0)
        return sum((covered & band).bit_count() * weight for band, weight in layers)

    def score(selected):
        return weighted(target[0], selected) + 3 * weighted(target[1], selected)

    feasible = [
        subset for length in range(len(paid_size) + 1)
        for subset in combinations(range(len(paid_size)), length)
        if cost_bani(sum(paid_size[i] for i in subset), length, 100, 2) <= budget
        and weighted(guard, subset) >= 2
    ]
    groups = [[(target[0], 1), (target[1], 3)], [(guard, 1)]]
    selected, upper, solved = solve_zone_coverage(
        paid_size, 100, 50, 2, budget, groups, (2,), layers, lambda: None, lambda: 2.0,
    )
    assert solved and selected in feasible
    assert len(selected) == expected_zones
    assert score(selected) == upper == max(map(score, feasible))
