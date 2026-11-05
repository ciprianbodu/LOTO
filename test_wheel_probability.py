from itertools import combinations
from math import comb

import pytest

from covering.probability import wheel_hit_probabilities
from wheeling_methods import generate_wheel


@pytest.mark.parametrize('tickets', [[], [[1, 2, 3]], [[1, 2, 3], [2, 3, 4]]])
def test_exact_probability_matches_exhaustive_draws(tickets):
    pool = [1, 2, 3, 4, 5, 6]
    draws = list(map(set, combinations(range(1, 10), 4)))
    actual = wheel_hit_probabilities(pool, tickets, 4, 9)
    for target in range(1, 5):
        expected_pool = sum(len(d & set(pool)) >= target for d in draws) / len(draws)
        expected_ticket = sum(
            any(len(d & set(t)) >= target for t in tickets) for d in draws
        ) / len(draws)
        assert actual['pool'][target] == pytest.approx(expected_pool)
        assert actual['ticket'][target] == pytest.approx(expected_ticket)


@pytest.mark.parametrize('pick,max_num', [(6, 49), (5, 40), (5, 45)])
def test_one_ticket_odds_do_not_grow_with_unplayed_pool(pick, max_num):
    ticket = list(range(1, pick + 1))
    small = wheel_hit_probabilities(range(1, 7), [ticket], pick, max_num)
    large = wheel_hit_probabilities(range(1, 17), [ticket], pick, max_num)
    assert large['ticket'] == small['ticket']
    assert large['pool'][3] > small['pool'][3]
    assert large['ticket'][pick] == pytest.approx(1 / comb(max_num, pick))


def test_conditional_cover_is_not_a_pool_hit_probability():
    pool = list(range(1, 7))
    wheel, cov = generate_wheel('lajolla', pool, 5, 4, condition=5)
    assert cov == 100
    odds = wheel_hit_probabilities(pool, wheel, 5, 40)
    assert odds['ticket'][4] < odds['pool'][4]
    # Repeated tickets cannot increase the chance of at least one win.
    assert wheel_hit_probabilities(pool, wheel * 2, 5, 40) == odds


def test_classical_full_coverage_guarantees_target_on_ticket():
    pool = list(range(1, 9))
    wheel, cov = generate_wheel('lajolla', pool, 6, 4)
    assert cov == 100
    odds = wheel_hit_probabilities(pool, wheel, 6, 49)
    assert odds['pool'][3] == odds['ticket'][3]
    assert odds['pool'][4] == odds['ticket'][4]
    assert odds['ticket'][6] == pytest.approx(len(wheel) / comb(49, 6))


@pytest.mark.parametrize('pool,tickets', [([1, 1], [[1]]), ([1, 2], [[3]]), ([1, 2], [[1, 1]])])
def test_invalid_tickets_are_not_given_odds(pool, tickets):
    with pytest.raises(ValueError):
        wheel_hit_probabilities(pool, tickets, 5, 40)
